#!/usr/bin/env bash
# Deploys the failure notifier. Safe to re-run: it updates what already exists.
#
# The webhook URL and API key come from Secrets Manager at runtime, so they
# are not passed to this script.
#
# Usage:
#   ./setup.sh --profile my-admin-profile
#   SECRET_ID="/other/secret" AWS_REGION="us-east-1" ./setup.sh --profile my-admin-profile
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

# ---------- Configuration ----------
# Secret holding the webhook "url" and "api_key" fields.
SECRET_ID="${SECRET_ID:-/myapp/iris-notification}"

FUNCTION_NAME="pdf-remediation-failure-notifier"
ROLE_NAME="${FUNCTION_NAME}-role"
FILTER_NAME="failure-notifier"
# Explicit failure matches. Filter patterns are case-sensitive; ? means OR.
# Edit this once you've seen what your code actually writes.
FILTER_PATTERN='?"Status: Failed" ?"Status: FAILED" ?"Status: failed"'
RESERVED_CONCURRENCY=2

LOG_GROUPS=(
    "/aws/lambda/PDFAccessibility-PdfChunkSplitterLambdaFDB27681-TfDtfjTyEwjs",
    "/aws/lambda/PDFAccessibility-PdfMergerLambda3075CEA9-wsiSWTIlDCFU",
    "/ecs/pdf-remediation/adobe-autotag",
    "/ecs/pdf-remediation/alt-text-generator"
)
# -----------------------------------

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
echo "Profile: $PROFILE  Account: $ACCOUNT_ID  Region: $REGION"

echo "==> Looking up secret $SECRET_ID"
# Also confirms the secret exists in this account/region before deploying.
SECRET_ARN="$(aws secretsmanager describe-secret --region "$REGION" --secret-id "$SECRET_ID" \
  --query ARN --output text)"
SECRET_KMS_KEY="$(aws secretsmanager describe-secret --region "$REGION" --secret-id "$SECRET_ID" \
  --query KmsKeyId --output text)"
echo "  $SECRET_ARN"

echo "==> Checking the filter pattern against sample lines (only failures should print)"
aws logs test-metric-filter --region "$REGION" \
  --filter-pattern "$FILTER_PATTERN" \
  --log-event-messages \
    "File: a.pdf, Status: succeeded" \
    "File: b.pdf, Status: Failed" \
    "File: c.pdf, Status: FAILED" \
  --query 'matches[].eventMessage' --output text

echo "==> IAM role"
if ROLE_ARN="$(aws iam get-role --role-name "$ROLE_NAME" --query Role.Arn --output text 2>/dev/null)"; then
  echo "  using existing role $ROLE_ARN"
else
  ROLE_ARN="$(aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "lambda.amazonaws.com"},
        "Action": "sts:AssumeRole"
      }]
    }' --query Role.Arn --output text)"
  echo "  created $ROLE_ARN"
  # Only needs to write its own logs.
  aws iam attach-role-policy --role-name "$ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
  NEW_ROLE=1
fi

# Inline policy: read this one secret only. Re-applied on every run.
SECRET_POLICY="$(SECRET_ARN="$SECRET_ARN" python3 -c '
import json, os
print(json.dumps({
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Action": "secretsmanager:GetSecretValue",
        "Resource": os.environ["SECRET_ARN"],
    }],
}))
')"
aws iam put-role-policy --role-name "$ROLE_NAME" \
  --policy-name read-iris-notification-secret \
  --policy-document "$SECRET_POLICY"
echo "  granted read access to the secret"

# Secrets encrypted with a customer-managed KMS key also need kms:Decrypt.
# (With the default aws/secretsmanager key, KmsKeyId is empty and nothing extra is needed.)
if [[ -n "$SECRET_KMS_KEY" && "$SECRET_KMS_KEY" != "None" ]]; then
  KMS_KEY_ARN="$(aws kms describe-key --region "$REGION" --key-id "$SECRET_KMS_KEY" \
    --query KeyMetadata.Arn --output text)"
  KMS_POLICY="$(KMS_KEY_ARN="$KMS_KEY_ARN" REGION="$REGION" python3 -c '
import json, os
print(json.dumps({
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Action": "kms:Decrypt",
        "Resource": os.environ["KMS_KEY_ARN"],
        "Condition": {"StringEquals": {
            "kms:ViaService": "secretsmanager." + os.environ["REGION"] + ".amazonaws.com"
        }},
    }],
}))
')"
  aws iam put-role-policy --role-name "$ROLE_NAME" \
    --policy-name decrypt-iris-notification-secret \
    --policy-document "$KMS_POLICY"
  echo "  granted kms:Decrypt on $KMS_KEY_ARN (customer-managed key)"
fi

if [[ "${NEW_ROLE:-0}" == "1" ]]; then
  echo "  waiting for IAM propagation..."
  sleep 15
fi

echo "==> Packaging"
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT
(cd "$SCRIPT_DIR" && python3 -m zipfile -c "$BUILD_DIR/function.zip" lambda_function.py)

# Only the secret name goes into the function's configuration, never the URL or key.
ENV_JSON="$(SECRET_ID="$SECRET_ID" python3 -c '
import json, os
print(json.dumps({"Variables": {"SECRET_ID": os.environ["SECRET_ID"]}}))
')"

echo "==> Lambda function"
if aws lambda get-function --function-name "$FUNCTION_NAME" --region "$REGION" >/dev/null 2>&1; then
  aws lambda update-function-code --region "$REGION" --function-name "$FUNCTION_NAME" \
    --zip-file "fileb://$BUILD_DIR/function.zip" >/dev/null
  aws lambda wait function-updated --region "$REGION" --function-name "$FUNCTION_NAME"
  aws lambda update-function-configuration --region "$REGION" --function-name "$FUNCTION_NAME" \
    --timeout 60 --environment "$ENV_JSON" >/dev/null
  aws lambda wait function-updated --region "$REGION" --function-name "$FUNCTION_NAME"
  echo "  updated $FUNCTION_NAME"
else
  aws lambda create-function --region "$REGION" \
    --function-name "$FUNCTION_NAME" \
    --runtime python3.12 \
    --handler lambda_function.handler \
    --role "$ROLE_ARN" \
    --zip-file "fileb://$BUILD_DIR/function.zip" \
    --timeout 60 \
    --memory-size 128 \
    --environment "$ENV_JSON" >/dev/null
  aws lambda wait function-active-v2 --region "$REGION" --function-name "$FUNCTION_NAME"
  echo "  created $FUNCTION_NAME"
fi

FUNCTION_ARN="$(aws lambda get-function --region "$REGION" --function-name "$FUNCTION_NAME" \
  --query Configuration.FunctionArn --output text)"

# Caps simultaneous pings during a failure storm. Can fail on new accounts
# with a low concurrency quota, so it's non-fatal.
aws lambda put-function-concurrency --region "$REGION" --function-name "$FUNCTION_NAME" \
  --reserved-concurrent-executions "$RESERVED_CONCURRENCY" >/dev/null \
  || echo "  WARNING: could not set reserved concurrency (check your account's Lambda concurrency quota)"

echo "==> Subscription filters"
for LG in "${LOG_GROUPS[@]}"; do
  echo "  $LG"
  FOUND="$(aws logs describe-log-groups --region "$REGION" --log-group-name-prefix "$LG" \
    --query "length(logGroups[?logGroupName=='$LG'])" --output text)"
  if [[ "$FOUND" != "1" ]]; then
    echo "    WARNING: log group not found, skipping (re-run after it exists)"
    continue
  fi

  # Allow CloudWatch Logs to invoke the function for this log group.
  STATEMENT_ID="cwlogs$(printf '%s' "$LG" | tr -c 'A-Za-z0-9_-' '-')"
  if ! OUT="$(aws lambda add-permission --region "$REGION" \
      --function-name "$FUNCTION_NAME" \
      --statement-id "$STATEMENT_ID" \
      --action lambda:InvokeFunction \
      --principal logs.amazonaws.com \
      --source-arn "arn:aws:logs:${REGION}:${ACCOUNT_ID}:log-group:${LG}:*" \
      --source-account "$ACCOUNT_ID" 2>&1)"; then
    if grep -q ResourceConflictException <<<"$OUT"; then
      echo "    permission already exists"
    else
      echo "$OUT" >&2
      exit 1
    fi
  fi

  # Same filter name overwrites, so re-running just updates the pattern.
  aws logs put-subscription-filter --region "$REGION" \
    --log-group-name "$LG" \
    --filter-name "$FILTER_NAME" \
    --filter-pattern "$FILTER_PATTERN" \
    --destination-arn "$FUNCTION_ARN"
  echo "    subscription filter set"
done

echo "Done. Run ./test.sh --profile $PROFILE to send a sample failure through the function."
