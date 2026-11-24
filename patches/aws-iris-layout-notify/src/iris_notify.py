"""
Notify the IRIS API (POST /v1/notify).

    secret  /myapp/iris-notification  ->  {"url": "...", "api_key": "..."}
    body    {"sender", "status", "file", "message", "payload": {...}}

The body follows IRIS's NotifyRequest schema (openapi/open-api.yaml): `file`
is the article's original upload name, the one IRIS polls /v1/status with,
and anything else goes in the free-form `payload` object.

Messages sent:
    "layout complete"    status success   (not yet in IRIS's enum, see README)
    "remediation error"  status error     (IRIS writes {file}.error)

Never raises: a failed notification is logged and the job result stands.
"""
import json
import os
import random
import time
import urllib.error
import urllib.request

import boto3

IRIS_SECRET_NAME = os.environ.get("IRIS_SECRET_NAME", "/myapp/iris-notification")
NOTIFY_ENABLED = os.environ.get("NOTIFY_IRIS", "true").lower() == "true"
SECRET_CACHE_SECONDS = 300  # pick up a rotated api_key without a redeploy
SENDER = "aws-iris-layout"

_config = {"value": None, "fetched_at": 0.0}


def _get_config():
    now = time.time()
    if _config["value"] is None or now - _config["fetched_at"] > SECRET_CACHE_SECONDS:
        resp = boto3.client("secretsmanager").get_secret_value(SecretId=IRIS_SECRET_NAME)
        secret = json.loads(resp["SecretString"])
        missing = [k for k in ("url", "api_key") if not secret.get(k)]
        if missing:
            raise KeyError(f"secret is missing field(s): {missing}")
        _config["value"] = {"url": secret["url"], "api_key": secret["api_key"]}
        _config["fetched_at"] = now
    return _config["value"]


def _post(url, api_key, body):
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"{api_key}",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status


def notify_iris(status, message, file, payload=None):
    if not NOTIFY_ENABLED:
        print(f"[INFO] IRIS notify disabled; would send {message} for {file}")
        return False
    try:
        cfg = _get_config()
    except Exception as e:
        print(f"[WARNING] {file}: cannot load IRIS config from {IRIS_SECRET_NAME}: {e}")
        return False
    body = {"sender": SENDER, "status": status, "file": file, "message": message}
    if payload:
        body["payload"] = payload
    data = json.dumps(body, default=str).encode("utf-8")
    err = None
    for attempt in range(1, 4):
        try:
            code = _post(cfg["url"], cfg["api_key"], data)
            print(f"[INFO] {file}: IRIS notified ({message}, HTTP {code})")
            return True
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                # IRIS understood the request and refused it; retrying won't help
                detail = e.read()[:300].decode("utf-8", "replace")
                print(f"[WARNING] {file}: IRIS rejected {message} (HTTP {e.code}): {detail}")
                return False
            err = e
        except Exception as e:  # network / 5xx
            err = e
        if attempt < 3:
            time.sleep(2 ** (attempt - 1) + random.uniform(0, 1))
    print(f"[WARNING] {file}: IRIS notification ({message}) failed after retries: {err}")
    return False
