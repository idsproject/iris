#!/usr/bin/env bash
# Invokes the deployed function with a fake CloudWatch Logs batch.
# This WILL send a real POST to the URL stored in the secret.
#
# Usage: ./test.sh --profile my-admin-profile
set -euo pipefail

# ---------- AWS profile ----------
# Every aws call below goes through this profile. Pass --profile NAME,
# or set AWS_PROFILE. The flag wins, and it also wins over any
# AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY already set in your shell.
PROFILE="${AWS_PROFILE:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--profile) PROFILE="${2:?--profile needs a value}"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done
if [[ -z "$PROFILE" ]]; then
  echo "No AWS profile given. Use --profile NAME (or set AWS_PROFILE)." >&2
  exit 1
fi
aws() { command aws --profile "$PROFILE" "$@"; }

REGION="${AWS_REGION:-$(aws configure get region || true)}"
if [[ -z "$REGION" ]]; then
  echo "No region for profile '$PROFILE'. Set AWS_REGION or add a region to the profile." >&2
  exit 1
fi
# ---------------------------------

FUNCTION_NAME="pdf-remediation-failure-notifier"
OUT="$(mktemp)"

EVENT="$(python3 -c '
import base64, gzip, json, time
now = int(time.time() * 1000)
data = {
    "messageType": "DATA_MESSAGE",
    "logGroup": "/aws/lambda/merger",
    "logStream": "test-stream",
    "subscriptionFilters": ["failure-notifier"],
    "logEvents": [
        {"id": "1", "timestamp": now, "message": "File: test-document.pdf, Status: Failed"},
        {"id": "2", "timestamp": now, "message": "File: other.pdf, Status: succeeded"},
        {"id": "3", "timestamp": now, "message": "2026-09-17 15:11:03,178 - ERROR - File: test-0000001, Status: Failed in First ECS task - Adobe API Error"},
    ],
}
encoded = base64.b64encode(gzip.compress(json.dumps(data).encode())).decode()
print(json.dumps({"awslogs": {"data": encoded}}))
')"

aws lambda invoke --region "$REGION" \
  --function-name "$FUNCTION_NAME" \
  --cli-binary-format raw-in-base64-out \
  --payload "$EVENT" \
  --log-type Tail \
  --query LogResult --output text "$OUT" | base64 --decode

echo
echo "Function returned: $(cat "$OUT")"
echo "Expected: {\"sent\": 2}"
rm -f "$OUT"
