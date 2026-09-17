"""
Notify an external server when a pdf-remediation pipeline step logs a failure.
Sends one POST per failed file.

Triggered by CloudWatch Logs subscription filters on each pipeline log group.

The webhook URL and API key are read from Secrets Manager. The secret must be
JSON with "url" and "api_key" fields.

Environment variables:
  SECRET_ID             (optional) secret name, default /myapp/iris-notification
  SECRET_CACHE_SECONDS  (optional) how long to reuse the secret, default 300
  WEBHOOK_TIMEOUT       (optional) request timeout in seconds, default 10
"""
import base64
import gzip
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3  # included in the Lambda Python runtime

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Mirrors the Insights query: parse @message "File: *, Status: *"
LINE = re.compile(r"File:\s*(?P<file>.*?),\s*Status:\s*(?P<status>[^\s,.;]+)")

# Safety net in case the subscription filter is ever loosened.
SUCCESS_STATUSES = {"succeeded"}

# Friendly names for each pipeline step, keyed by log group.
STEP_NAMES = {
    "/aws/lambda/PDFAccessibility-PdfChunkSplitterLambdaFDB27681-TfDtfjTyEwjs": "chunker",
    "/aws/lambda/PDFAccessibility-PdfMergerLambda3075CEA9-wsiSWTIlDCFU": "merger",
    "/ecs/pdf-remediation/adobe-autotag": "adobe-autotag",
    "/ecs/pdf-remediation/alt-text-generator": "alt-text-generator",
}

SECRET_ID = os.environ.get("SECRET_ID", "/myapp/iris-notification")
SECRET_CACHE_SECONDS = int(os.environ.get("SECRET_CACHE_SECONDS", "300"))

# Created once per container and reused across invocations.
_secrets_client = boto3.client("secretsmanager")
_secret_cache = {"value": None, "fetched_at": 0.0}

# Pings go out in parallel so a large batch doesn't hit the Lambda timeout.
MAX_PARALLEL_PINGS = 5


def decode_payload(event):
    """CloudWatch Logs delivers events base64-encoded and gzipped."""
    return json.loads(gzip.decompress(base64.b64decode(event["awslogs"]["data"])))


def get_webhook_config():
    """Read url and api_key from Secrets Manager, cached for a few minutes.

    The cache avoids a Secrets Manager call on every invocation, while still
    picking up a rotated key within SECRET_CACHE_SECONDS.
    """
    now = time.time()
    if _secret_cache["value"] is None or now - _secret_cache["fetched_at"] > SECRET_CACHE_SECONDS:
        secret = json.loads(
            _secrets_client.get_secret_value(SecretId=SECRET_ID)["SecretString"]
        )
        missing = [k for k in ("url", "api_key") if not secret.get(k)]
        if missing:
            raise KeyError(f"Secret {SECRET_ID} is missing field(s): {missing}")
        _secret_cache["value"] = {"url": secret["url"], "api_key": secret["api_key"]}
        _secret_cache["fetched_at"] = now
    return _secret_cache["value"]


def clear_secret_cache():
    _secret_cache["value"] = None


def extract_failures(log_events):
    """Return one entry per failed file in the batch."""
    failures = []
    seen_files = set()
    for e in log_events:
        log_line = e.get("message", "")
        m = LINE.search(log_line)
        if m:
            status = m.group("status").strip()
            if status.lower() in SUCCESS_STATUSES:
                continue
            file_name = m.group("file").strip()
            # The same file logged as failed twice in one batch gets one ping.
            if file_name in seen_files:
                continue
            seen_files.add(file_name)
        else:
            # The filter matched but the line isn't in the expected format.
            # Report it anyway rather than silently dropping a failure.
            file_name, status = "unknown", "unknown"
        failures.append({
            "file": file_name,
            "reported_status": status,
            "timestamp": e.get("timestamp"),
            "log_line": log_line[:500],
        })
    return failures


def post_to_webhook(body, config):
    req = urllib.request.Request(
        config["url"],
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"{config['api_key']}",
        },
        method="POST",
    )
    timeout = float(os.environ.get("WEBHOOK_TIMEOUT", "10"))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        logger.info("Webhook responded with HTTP %s for %s", resp.status, body["file"])


def handler(event, context):
    payload = decode_payload(event)

    # CloudWatch sends a CONTROL_MESSAGE when the filter is created; ignore it.
    if payload.get("messageType") != "DATA_MESSAGE":
        logger.info("Skipping %s", payload.get("messageType"))
        return {"sent": 0}

    failures = extract_failures(payload.get("logEvents", []))
    if not failures:
        return {"sent": 0}

    log_group = payload.get("logGroup", "unknown")
    step = STEP_NAMES.get(log_group, log_group)
    pings = [
        {
            "status": "failed",
            "sender": "aws",
            "file": failure["file"],
            "message": "remediation error",
            "payload": {
                "step": step,
                "log_group": log_group,
                "log_stream": payload.get("logStream"),
                "reported_status": failure["reported_status"],
                "timestamp": failure["timestamp"],
                "log_line": failure["log_line"],
            },
        }
        for failure in failures
    ]
    logger.info("Reporting %d failed file(s) from %s", len(pings), log_group)

    config = get_webhook_config()

    failed_pings = []
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_PINGS) as pool:
        futures = {pool.submit(post_to_webhook, p, config): p["file"] for p in pings}
        for future in as_completed(futures):
            try:
                future.result()
            except (urllib.error.URLError, TimeoutError) as err:
                logger.error("Webhook call failed for %s: %s", futures[future], err)
                failed_pings.append(futures[future])

    if failed_pings:
        # Re-read the secret on the retry, in case the URL or key was changed.
        clear_secret_cache()
        # Raising makes Lambda retry the whole batch (2 retries by default).
        raise RuntimeError(
            f"{len(failed_pings)} of {len(pings)} pings failed: {failed_pings}"
        )

    return {"sent": len(pings)}
