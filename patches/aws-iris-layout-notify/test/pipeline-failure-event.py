#!/usr/bin/env python3
"""
Print a CloudWatch Logs subscription event like the one Pdf2HtmlPipeline's
log sends when it fails an article, for a manual run of iris-layout:

    python3 test/pipeline-failure-event.py "uploads/My Article.pdf" > failure.json
    aws lambda invoke --function-name iris-layout --cli-binary-format raw-in-base64-out \
        --payload file://failure.json out.json && cat out.json

This sends a real "remediation error" for that file to IRIS.
"""
import base64
import gzip
import json
import sys
import time

key = sys.argv[1] if len(sys.argv) > 1 else "uploads/test-document.pdf"
now = int(time.time() * 1000)
data = {
    "messageType": "DATA_MESSAGE",
    "logGroup": "/aws/lambda/Pdf2HtmlPipeline",
    "logStream": "manual-test",
    "subscriptionFilters": ["iris-layout-pipeline-failures"],
    "logEvents": [
        {"id": "1", "timestamp": now, "message": f"[ERROR] Processing {key} failed: manual test\n"},
    ],
}
print(json.dumps({"awslogs": {"data": base64.b64encode(gzip.compress(json.dumps(data).encode())).decode()}}))
