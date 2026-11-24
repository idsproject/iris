"""
Tell IRIS when the ASU PDF-to-HTML pipeline (Pdf2HtmlPipeline) fails an
article.

Pdf2HtmlPipeline catches its own exceptions and returns normally, so Lambda
never sees a failed invocation and on-failure destinations never fire. Its log
is the only signal. A CloudWatch Logs subscription filter on
/aws/lambda/Pdf2HtmlPipeline (set up by deploy.sh) sends these lines here:

    [ERROR] Failed to download s3://<bucket>/<key>: <error>      names the key
    [ERROR] Processing <key> failed: <error>                     names the key
    [ERROR] Creating or uploading zip failed: <error>            no key
    [ERROR] Unhandled exception: <error>                         no key
    <time> <request id> Task timed out after 900.00 seconds      no key
    ... Runtime exited with error: signal: killed                no key (out of memory)

The first four are the pipeline's own print() calls. They have a space after
"[ERROR]"; lines from Python's logging module have a tab, so a library
logging an error it recovers from is not mistaken for a failed article.

For lines without a key, the key comes from the same log stream: the
pipeline prints "[INFO] Processing s3://<bucket>/<key>" at the start of every
invocation, and a stream runs one invocation at a time, so the last such line
after the last "START RequestId" belongs to the failed run.

The key is uploads/<original name>.pdf (the pipeline skips anything in a
subfolder), so IRIS gets back the file name it uploaded.
"""
import os
import re
import time

import boto3

from iris_notify import notify_iris

UPLOADS_PREFIX = os.environ.get("UPLOADS_PREFIX", "uploads/")
# Pdf2HtmlPipeline's timeout is 15 minutes; look a little further back.
LOOKBACK_MS = int(os.environ.get("LOOKBACK_MINUTES", "16")) * 60 * 1000

# (pattern, has key). Pdf2HtmlPipeline URL-decodes the key before printing it.
FAILURE_LINES = [
    (re.compile(r"\[ERROR\] Failed to download s3://[^/\s]+/(?P<key>.+?\.pdf): (?P<error>.*)", re.I), True),
    (re.compile(r"\[ERROR\] Processing (?P<key>.+?\.pdf) failed: (?P<error>.*)", re.I), True),
    (re.compile(r"\[ERROR\] (?P<error>Creating or uploading zip failed: .*)"), False),
    (re.compile(r"\[ERROR\] (?P<error>Unhandled exception: .*)"), False),
    (re.compile(r"(?P<error>Task timed out after .*)"), False),
    (re.compile(r"(?P<error>Runtime exited with error.*)"), False),
]
PROCESSING_LINE = re.compile(r"\[INFO\] Processing s3://[^/\s]+/(?P<key>.+)")

# Must match every FAILURE_LINES pattern; deploy.sh tests it with sample lines.
SUBSCRIPTION_FILTER = ('?"[ERROR] Failed to download" ?"[ERROR] Processing" '
                       '?"[ERROR] Creating or uploading zip failed" ?"[ERROR] Unhandled exception" '
                       '?"Task timed out after" ?"Runtime exited with error"')
LOOKBACK_FILTER = '?"START RequestId" ?"[INFO] Processing s3://"'

_logs = None


def logs_client():
    global _logs
    if _logs is None:
        _logs = boto3.client("logs")
    return _logs


def match_failure(line):
    """Return (key or None, error text), or None if the line is not a failure."""
    for pattern, has_key in FAILURE_LINES:
        m = pattern.search(line)
        if m:
            return (m.group("key").strip() if has_key else None), m.group("error").strip()
    return None


def key_from_stream(log_group, log_stream, timestamp):
    """The key of the invocation that was running in log_stream at timestamp."""
    for attempt in range(3):
        if attempt:
            time.sleep(2)  # the earlier lines may not be searchable yet
        key = None
        kwargs = {"logGroupName": log_group, "logStreamNames": [log_stream],
                  "startTime": max(0, timestamp - LOOKBACK_MS), "endTime": timestamp,
                  "filterPattern": LOOKBACK_FILTER}
        while True:
            page = logs_client().filter_log_events(**kwargs)
            for ev in page.get("events", []):
                msg = ev.get("message", "")
                if "START RequestId" in msg:
                    key = None  # a new invocation; earlier keys belong to earlier runs
                    continue
                m = PROCESSING_LINE.search(msg)
                if m:
                    key = m.group("key").strip()
            if not page.get("nextToken"):
                break
            kwargs["nextToken"] = page["nextToken"]
        if key:
            return key
    return None


def handle(payload):
    """payload: the decoded CloudWatch Logs subscription data."""
    if payload.get("messageType") != "DATA_MESSAGE":
        print(f"[INFO] skip {payload.get('messageType')}")
        return {"sent": 0}
    log_group = payload.get("logGroup", "")
    log_stream = payload.get("logStream", "")

    failures = {}  # file -> payload; one ping per file per batch
    for ev in payload.get("logEvents", []):
        line = ev.get("message", "")
        hit = match_failure(line)
        if not hit:
            continue
        key, error = hit
        if key is None:
            try:
                key = key_from_stream(log_group, log_stream, ev.get("timestamp", 0))
            except Exception as e:
                print(f"[WARNING] cannot search {log_group} for the failed file: {e}")
        if not key or not key.startswith(UPLOADS_PREFIX) or not key.lower().endswith(".pdf"):
            # Nothing IRIS could match to a request; leave a trace in our log instead.
            print(f"[WARNING] pipeline failure with no uploads/ key (key={key!r}): {line[:500]}")
            continue
        file = key[len(UPLOADS_PREFIX):]
        failures.setdefault(file, {
            "step": "pdf2html", "source_key": key, "error": error[:500],
            "log_group": log_group, "log_stream": log_stream,
            "timestamp": ev.get("timestamp"), "log_line": line[:500],
        })

    sent = 0
    for file, details in failures.items():
        print(f"[INFO] {file}: Pdf2HtmlPipeline failed: {details['error']}")
        if notify_iris(status="error", message="remediation error", file=file, payload=details):
            sent += 1
    return {"failures": len(failures), "sent": sent}
