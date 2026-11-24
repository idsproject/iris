#!/usr/bin/env bash
# Deploy (or update) the iris-layout Lambda next to the ASU pdf2html pipeline.
#
#   BUCKET=<pdf2html bucket name> ./deploy.sh
#
# Safe to re-run: it updates code/config in place, and re-adds the S3 trigger
# if a later ASU redeploy ever removed it. Run it from AWS CloudShell in the
# account/region where the pdf2html stack lives.
#
# Optional settings (environment variables):
#   FN=iris-layout              function name
#   ROLE_NAME=iris-layout-role  IAM role name
#   PROFILE=...                 AWS CLI profile (default: current credentials)
#   REGION=...                  default: the CLI's configured region
#   OUTPUT_PREFIX=iris/         where results are written in the bucket
#   IRIS_SECRET_NAME=/myapp/iris-notification
#   NOTIFY_IRIS=true            set false to skip IRIS notifications
#   MEMORY=2048  TIMEOUT=300  TMP_MB=2048
set -euo pipefail
export LC_ALL=C

BUCKET="${BUCKET:?set BUCKET=<pdf2html bucket name>}"
FN="${FN:-iris-layout}"
ROLE_NAME="${ROLE_NAME:-iris-layout-role}"
PROFILE="${PROFILE:-}"
OUTPUT_PREFIX="${OUTPUT_PREFIX:-iris/}"
IRIS_SECRET_NAME="${IRIS_SECRET_NAME:-/myapp/iris-notification}"
NOTIFY_IRIS="${NOTIFY_IRIS:-true}"
MEMORY="${MEMORY:-2048}"
TIMEOUT="${TIMEOUT:-300}"
TMP_MB="${TMP_MB:-2048}"
RUNTIME=python3.13
PYVER=3.13
TRIGGER_ID="iris-layout-trigger"
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

AWS=(aws)
[[ -n "$PROFILE" ]] && AWS+=(--profile "$PROFILE")
REGION="${REGION:-$("${AWS[@]}" configure get region || true)}"
REGION="${REGION:-${AWS_REGION:-${AWS_DEFAULT_REGION:-}}}"
[[ -z "$REGION" ]] && { echo "Set REGION=..." >&2; exit 1; }
AWS+=(--region "$REGION")

ACCOUNT=$("${AWS[@]}" sts get-caller-identity --query Account --output text)
PARTITION=$("${AWS[@]}" sts get-caller-identity --query Arn --output text | cut -d: -f2)
echo "Account $ACCOUNT  Region $REGION  Bucket $BUCKET  Function $FN"
"${AWS[@]}" s3api head-bucket --bucket "$BUCKET" >/dev/null

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# ---------------------------------------------------------------- 1. build
echo "== Building package (Linux x86_64, Python $PYVER)"
python3 -m pip install --quiet --upgrade --target "$WORK/build" \
    --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64 \
    --implementation cp --python-version "$PYVER" --only-binary=:all: \
    -r "$HERE/requirements.txt"
python3 -m pip install --quiet --upgrade --target "$WORK/build" --no-deps \
    --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64 \
    --implementation cp --python-version "$PYVER" --only-binary=:all: \
    -r "$HERE/requirements-nodeps.txt"
cp "$HERE"/src/*.py "$WORK/build/"
cp -r "$HERE/src/fonts" "$WORK/build/fonts"
find "$WORK/build" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$WORK/build" -path '*/numpy/*/tests' -type d -prune -exec rm -rf {} + 2>/dev/null || true
UNZIPPED_MB=$(du -sm "$WORK/build" | cut -f1)
if (( UNZIPPED_MB > 245 )); then
    echo "Package is ${UNZIPPED_MB} MB unzipped; Lambda's limit is 250 MB." >&2
    exit 1
fi
(cd "$WORK/build" && zip -qry "$WORK/iris-layout.zip" .)
echo "   package: $(du -h "$WORK/iris-layout.zip" | cut -f1) zipped, ${UNZIPPED_MB} MB unzipped"

# ---------------------------------------------------------------- 2. IAM role
ROLE_ARN="arn:$PARTITION:iam::$ACCOUNT:role/$ROLE_NAME"
if ! "${AWS[@]}" iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
    echo "== Creating role $ROLE_NAME"
    "${AWS[@]}" iam create-role --role-name "$ROLE_NAME" \
        --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
    "${AWS[@]}" iam attach-role-policy --role-name "$ROLE_NAME" \
        --policy-arn "arn:$PARTITION:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
    NEW_ROLE=1
fi
echo "== Updating role permissions"
cat > "$WORK/policy.json" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Sid": "ReadPipelineFiles", "Effect": "Allow", "Action": "s3:GetObject",
     "Resource": ["arn:$PARTITION:s3:::$BUCKET/uploads/*",
                  "arn:$PARTITION:s3:::$BUCKET/output/*",
                  "arn:$PARTITION:s3:::$BUCKET/remediated/*",
                  "arn:$PARTITION:s3:::$BUCKET/$OUTPUT_PREFIX*"]},
    {"Sid": "WriteResults", "Effect": "Allow", "Action": "s3:PutObject",
     "Resource": "arn:$PARTITION:s3:::$BUCKET/$OUTPUT_PREFIX*"},
    {"Sid": "ListBucket", "Effect": "Allow", "Action": "s3:ListBucket",
     "Resource": "arn:$PARTITION:s3:::$BUCKET"},
    {"Sid": "IrisSecret", "Effect": "Allow", "Action": "secretsmanager:GetSecretValue",
     "Resource": "arn:$PARTITION:secretsmanager:$REGION:$ACCOUNT:secret:$IRIS_SECRET_NAME-*"}
  ]
}
EOF
"${AWS[@]}" iam put-role-policy --role-name "$ROLE_NAME" --policy-name iris-layout-access \
    --policy-document "file://$WORK/policy.json"
[[ -n "${NEW_ROLE:-}" ]] && { echo "   waiting for new role to propagate"; sleep 15; }

# ---------------------------------------------------------------- 3. code upload
CODE_KEY="iris-deploy/iris-layout-$(date +%Y%m%d%H%M%S).zip"   # prefix triggers nothing
"${AWS[@]}" s3 cp --quiet "$WORK/iris-layout.zip" "s3://$BUCKET/$CODE_KEY"

ENVVARS="Variables={OUTPUT_PREFIX=$OUTPUT_PREFIX,IRIS_SECRET_NAME=$IRIS_SECRET_NAME,NOTIFY_IRIS=$NOTIFY_IRIS}"

# ---------------------------------------------------------------- 4. function
if "${AWS[@]}" lambda get-function --function-name "$FN" >/dev/null 2>&1; then
    echo "== Updating function $FN"
    "${AWS[@]}" lambda update-function-code --function-name "$FN" \
        --s3-bucket "$BUCKET" --s3-key "$CODE_KEY" --publish >/dev/null
    "${AWS[@]}" lambda wait function-updated-v2 --function-name "$FN"
    "${AWS[@]}" lambda update-function-configuration --function-name "$FN" \
        --runtime "$RUNTIME" --handler lambda_function.handler --role "$ROLE_ARN" \
        --memory-size "$MEMORY" --timeout "$TIMEOUT" --ephemeral-storage "Size=$TMP_MB" \
        --environment "$ENVVARS" >/dev/null
else
    echo "== Creating function $FN"
    for i in 1 2 3 4 5; do   # a brand-new role can take a moment to be assumable
        if "${AWS[@]}" lambda create-function --function-name "$FN" \
            --runtime "$RUNTIME" --architectures x86_64 --handler lambda_function.handler \
            --role "$ROLE_ARN" --code "S3Bucket=$BUCKET,S3Key=$CODE_KEY" \
            --memory-size "$MEMORY" --timeout "$TIMEOUT" --ephemeral-storage "Size=$TMP_MB" \
            --environment "$ENVVARS" \
            --description "IRIS: page-faithful accessible HTML + Prince print source from pdf2html output" \
            >/dev/null; then break; fi
        echo "   retrying in 10s"; sleep 10
    done
fi
"${AWS[@]}" lambda wait function-updated-v2 --function-name "$FN"
FN_ARN=$("${AWS[@]}" lambda get-function --function-name "$FN" --query Configuration.FunctionArn --output text)

# No automatic retries: a failed article fails the same way again, and each
# attempt would send IRIS another "layout failed".
"${AWS[@]}" lambda put-function-event-invoke-config --function-name "$FN" \
    --maximum-retry-attempts 0 >/dev/null

# ---------------------------------------------------------------- 5. S3 trigger
"${AWS[@]}" lambda add-permission --function-name "$FN" --statement-id s3-invoke-iris-layout \
    --action lambda:InvokeFunction --principal s3.amazonaws.com \
    --source-arn "arn:$PARTITION:s3:::$BUCKET" --source-account "$ACCOUNT" >/dev/null 2>&1 || true

echo "== Adding S3 trigger (keeping the bucket's existing triggers)"
"${AWS[@]}" s3api get-bucket-notification-configuration --bucket "$BUCKET" > "$WORK/notif.json"
cp "$WORK/notif.json" "$HERE/notification-backup-$(date +%Y%m%d%H%M%S).json"
python3 - "$WORK/notif.json" "$FN_ARN" "$TRIGGER_ID" > "$WORK/notif-new.json" <<'PY'
import json, sys
path, arn, tid = sys.argv[1:4]
raw = open(path).read().strip()
cfg = json.loads(raw) if raw else {}
cfg.pop("ResponseMetadata", None)
lam = [c for c in cfg.get("LambdaFunctionConfigurations", []) if c.get("Id") != tid]
lam.append({
    "Id": tid,
    "LambdaFunctionArn": arn,
    "Events": ["s3:ObjectCreated:*"],
    "Filter": {"Key": {"FilterRules": [
        {"Name": "Prefix", "Value": "remediated/final_"},
        {"Name": "Suffix", "Value": ".zip"}]}},
})
cfg["LambdaFunctionConfigurations"] = lam
print(json.dumps(cfg))
PY
"${AWS[@]}" s3api put-bucket-notification-configuration --bucket "$BUCKET" \
    --notification-configuration "file://$WORK/notif-new.json"

echo "== Bucket triggers now:"
"${AWS[@]}" s3api get-bucket-notification-configuration --bucket "$BUCKET" \
    --query 'LambdaFunctionConfigurations[].[Id,LambdaFunctionArn,Filter.Key.FilterRules[0].Value]' --output table

echo
echo "Done. Results for each article will appear in s3://$BUCKET/$OUTPUT_PREFIX"
echo "Logs: aws logs tail /aws/lambda/$FN --follow --region $REGION"
