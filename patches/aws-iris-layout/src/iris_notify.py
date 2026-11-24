"""
Notify the IRIS API (POST /v1/notify) when a layout job finishes.

Same Secrets Manager secret and payload shape as the title-generator
iris_hook.py (patches/aws-iris-notify-patch):

    secret  /myapp/iris-notification  ->  {"url": "...", "api_key": "..."}
    body    {"sender", "file", "status", "message", ...extra fields}

Messages sent: "layout complete" (status success) and "layout failed"
(status error). Extra fields (bucket, key, ...) are ignored by IRIS's current
Notify struct until it is extended to use them.

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
SENDER = "aws-iris-layout"

_config = None


def _get_config():
    global _config
    if _config is None:
        resp = boto3.client("secretsmanager").get_secret_value(SecretId=IRIS_SECRET_NAME)
        _config = json.loads(resp["SecretString"])
    return _config


def _post(url, api_key, body):
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"{api_key}",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status


def notify_iris(status, message, file, extra=None):
    if not NOTIFY_ENABLED:
        print(f"[INFO] IRIS notify disabled; would send {message} for {file}")
        return False
    try:
        cfg = _get_config()
        url, key = cfg["url"], cfg["api_key"]
    except Exception as e:
        print(f"[WARNING] {file}: cannot load IRIS config from {IRIS_SECRET_NAME}: {e}")
        return False
    payload = {"sender": SENDER, "file": file, "status": status, "message": message}
    payload.update(extra or {})
    body = json.dumps(payload).encode("utf-8")
    for attempt in range(1, 4):
        try:
            code = _post(url, key, body)
            print(f"[INFO] {file}: IRIS notified ({message}, HTTP {code})")
            return True
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                # IRIS understood the request and refused it; retrying won't help
                detail = e.read()[:300].decode("utf-8", "replace")
                print(f"[WARNING] {file}: IRIS rejected notification (HTTP {e.code}): {detail}")
                return False
            err = e
        except Exception as e:  # network / 5xx
            err = e
        if attempt < 3:
            time.sleep(2 ** (attempt - 1) + random.uniform(0, 1))
    print(f"[WARNING] {file}: IRIS notification failed after retries: {err}")
    return False
