#!/usr/bin/env bash
# Deploy (or update) the iris-layout Lambda next to the ASU pdf2html pipeline:
# the layout build on each finished article, and IRIS notifications for
# "layout complete" and "remediation error".
#
#   BUCKET=<pdf2html bucket name> ./deploy.sh
#
# Safe to re-run: it updates code/config in place, and re-adds the S3 trigger
# and log subscription if a later ASU redeploy ever removed them. Run it from
# AWS CloudShell in the account/region where the pdf2html stack lives.
#
# Optional settings (environment variables):
#   FN=iris-layout              function name
#   ROLE_NAME=iris-layout-role  IAM role name
#   PROFILE=...                 AWS CLI profile (default: current credentials)
#   REGION=...                  default: the CLI's configured region
#   OUTPUT_PREFIX=iris/         where results are written in the bucket
#   IRIS_SECRET_NAME=/myapp/iris-notification
#   NOTIFY_IRIS=true            set false to skip IRIS notifications
#   WATCH_PIPELINE=true         set false to skip Pdf2HtmlPipeline failure notifications
#   PIPELINE_LOG_GROUP=/aws/lambda/Pdf2HtmlPipeline
#   MEMORY=2048  TIMEOUT=300  TMP_MB=2048
#   VENV=./patchenv             Python virtualenv used to build (created if missing)
set -euo pipefail
export LC_ALL=C

BUCKET="${BUCKET:?set BUCKET=<pdf2html bucket name>}"
FN="${FN:-iris-layout}"
ROLE_NAME="${ROLE_NAME:-iris-layout-role}"
PROFILE="${PROFILE:-}"
OUTPUT_PREFIX="${OUTPUT_PREFIX:-iris/}"
IRIS_SECRET_NAME="${IRIS_SECRET_NAME:-/myapp/iris-notification}"
NOTIFY_IRIS="${NOTIFY_IRIS:-true}"
WATCH_PIPELINE="${WATCH_PIPELINE:-true}"
PIPELINE_LOG_GROUP="${PIPELINE_LOG_GROUP:-/aws/lambda/Pdf2HtmlPipeline}"
MEMORY="${MEMORY:-2048}"
TIMEOUT="${TIMEOUT:-300}"
TMP_MB="${TMP_MB:-2048}"
RUNTIME=python3.13
PYVER=3.13
TRIGGER_ID="iris-layout-trigger"
FILTER_NAME="iris-layout-pipeline-failures"
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# The function stops a layout 45 s before its timeout to tell IRIS it failed.
(( TIMEOUT >= 120 )) || { echo "TIMEOUT must be at least 120 seconds" >&2; exit 1; }

# All Python below runs from this virtualenv. Only its pip matters: the
# libraries are installed into the Lambda package, not into the venv.
VENV="${VENV:-$HERE/patchenv}"
if [[ ! -x "$VENV/bin/python" ]]; then
    echo "== Creating virtualenv $VENV"
    python3 -m venv "$VENV"
fi
PY="$VENV/bin/python"
"$PY" -m pip --version >/dev/null 2>&1 || {
    echo "No working pip in $VENV; recreate it with: python3 -m venv --clear $VENV" >&2; exit 1; }

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

# ---------------------------------------------------------------- 0. checks
SECRET_ARN="" SECRET_KMS_ARN=""
if [[ "$NOTIFY_IRIS" == "true" ]]; then
    echo "== Looking up secret $IRIS_SECRET_NAME"
    if ! SECRET_ARN=$("${AWS[@]}" secretsmanager describe-secret --secret-id "$IRIS_SECRET_NAME" \
            --query ARN --output text); then
        echo "Secret not found. Create it ({\"url\": ..., \"api_key\": ...}) or deploy with NOTIFY_IRIS=false." >&2
        exit 1
    fi
    SECRET_KMS=$("${AWS[@]}" secretsmanager describe-secret --secret-id "$IRIS_SECRET_NAME" \
        --query KmsKeyId --output text)
    # The default aws/secretsmanager key needs no extra permission; a customer-managed key does.
    if [[ -n "$SECRET_KMS" && "$SECRET_KMS" != "None" ]]; then
        SECRET_KMS_ARN=$("${AWS[@]}" kms describe-key --key-id "$SECRET_KMS" --query KeyMetadata.Arn --output text)
    fi
fi

FILTER_PATTERN=$("$PY" - "$HERE/src/pipeline_failures.py" <<'PY'
import ast, sys
for node in ast.parse(open(sys.argv[1]).read()).body:
    if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "SUBSCRIPTION_FILTER" for t in node.targets):
        print(ast.literal_eval(node.value))
PY
)
[[ -n "$FILTER_PATTERN" ]] || { echo "SUBSCRIPTION_FILTER not found in src/pipeline_failures.py" >&2; exit 1; }
if [[ "$WATCH_PIPELINE" == "true" ]]; then
    echo "== Checking the log filter against sample Pdf2HtmlPipeline lines"
    # Passed as JSON: the CLI may read a list argument starting with "[" as JSON.
    "$PY" - "$FILTER_PATTERN" > "$WORK/filter-test.json" <<'PY'
import json, sys
print(json.dumps({"filterPattern": sys.argv[1], "logEventMessages": [
    "[ERROR] Failed to download s3://b/uploads/a.pdf: An error occurred (403)",
    "[ERROR] Processing uploads/a.pdf failed: BDA job failed",
    "[ERROR] Creating or uploading zip failed: disk full",
    "[ERROR] Unhandled exception: boom",
    "2026-11-24T10:00:00.000Z 0f1e2d3c Task timed out after 900.00 seconds",
    "RequestId: 0f1e2d3c Error: Runtime exited with error: signal: killed",
    # these must not match
    "[INFO] Processing s3://b/uploads/a.pdf",
    "START RequestId: 0f1e2d3c Version: $LATEST",
]}))
PY
    MATCHED=$("${AWS[@]}" logs test-metric-filter --cli-input-json "file://$WORK/filter-test.json" \
        --query 'length(matches)' --output text)
    if [[ "$MATCHED" != "6" ]]; then
        echo "Filter pattern matched $MATCHED of the 6 failure samples (and should match no others):" >&2
        echo "  $FILTER_PATTERN" >&2
        exit 1
    fi
    echo "   ok (6 of 8 sample lines match, as expected)"
fi

# ---------------------------------------------------------------- 1. build
echo "== Building package (Linux x86_64, Python $PYVER)"
"$PY" -m pip install --quiet --upgrade --target "$WORK/build" \
    --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64 \
    --implementation cp --python-version "$PYVER" --only-binary=:all: \
    -r "$HERE/requirements.txt"
"$PY" -m pip install --quiet --upgrade --target "$WORK/build" --no-deps \
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
"$PY" - "$PARTITION" "$REGION" "$ACCOUNT" "$BUCKET" "$OUTPUT_PREFIX" "$PIPELINE_LOG_GROUP" \
        "$SECRET_ARN" "$SECRET_KMS_ARN" > "$WORK/policy.json" <<'PY'
import json, sys
part, region, account, bucket, out, log_group, secret_arn, kms_arn = sys.argv[1:9]
s3 = f"arn:{part}:s3:::{bucket}"
lg = f"arn:{part}:logs:{region}:{account}:log-group:{log_group}"
stmts = [
    {"Sid": "ReadPipelineFiles", "Effect": "Allow", "Action": "s3:GetObject",
     "Resource": [f"{s3}/uploads/*", f"{s3}/output/*", f"{s3}/remediated/*", f"{s3}/{out}*"]},
    {"Sid": "WriteResults", "Effect": "Allow", "Action": "s3:PutObject", "Resource": f"{s3}/{out}*"},
    {"Sid": "ListBucket", "Effect": "Allow", "Action": "s3:ListBucket", "Resource": s3},
    # find which article a failed Pdf2HtmlPipeline run was processing
    {"Sid": "ReadPipelineLog", "Effect": "Allow", "Action": "logs:FilterLogEvents",
     "Resource": [lg, f"{lg}:*"]},
]
if secret_arn:
    stmts.append({"Sid": "IrisSecret", "Effect": "Allow", "Action": "secretsmanager:GetSecretValue",
                  "Resource": secret_arn})
if kms_arn:
    stmts.append({"Sid": "IrisSecretKey", "Effect": "Allow", "Action": "kms:Decrypt", "Resource": kms_arn,
                  "Condition": {"StringEquals": {"kms:ViaService": f"secretsmanager.{region}.amazonaws.com"}}})
print(json.dumps({"Version": "2012-10-17", "Statement": stmts}, indent=2))
PY
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
            --description "IRIS: page-faithful accessible HTML + Prince print source from pdf2html output; IRIS notifications" \
            >/dev/null; then break; fi
        (( i == 5 )) && { echo "Could not create $FN" >&2; exit 1; }
        echo "   retrying in 10s"; sleep 10
    done
fi
"${AWS[@]}" lambda wait function-updated-v2 --function-name "$FN"
FN_ARN=$("${AWS[@]}" lambda get-function --function-name "$FN" --query Configuration.FunctionArn --output text)

# No automatic retries: a failed article fails the same way again, and each
# attempt would send IRIS another "remediation error".
"${AWS[@]}" lambda put-function-event-invoke-config --function-name "$FN" \
    --maximum-retry-attempts 0 >/dev/null

# ---------------------------------------------------------------- 5. S3 trigger
"${AWS[@]}" lambda add-permission --function-name "$FN" --statement-id s3-invoke-iris-layout \
    --action lambda:InvokeFunction --principal s3.amazonaws.com \
    --source-arn "arn:$PARTITION:s3:::$BUCKET" --source-account "$ACCOUNT" >/dev/null 2>&1 || true

echo "== Adding S3 trigger (keeping the bucket's existing triggers)"
"${AWS[@]}" s3api get-bucket-notification-configuration --bucket "$BUCKET" > "$WORK/notif.json"
cp "$WORK/notif.json" "$HERE/notification-backup-$(date +%Y%m%d%H%M%S).json"
"$PY" - "$WORK/notif.json" "$FN_ARN" "$TRIGGER_ID" > "$WORK/notif-new.json" <<'PY'
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

# ---------------------------------------------------------------- 6. pipeline failures
if [[ "$WATCH_PIPELINE" == "true" ]]; then
    echo "== Subscribing to $PIPELINE_LOG_GROUP failures"
    FOUND=$("${AWS[@]}" logs describe-log-groups --log-group-name-prefix "$PIPELINE_LOG_GROUP" \
        --query "length(logGroups[?logGroupName=='$PIPELINE_LOG_GROUP'])" --output text)
    if [[ "$FOUND" != "1" ]]; then
        echo "   WARNING: log group not found (Pdf2HtmlPipeline has never run?). Re-run after it exists."
    else
        if ! OUT=$("${AWS[@]}" lambda add-permission --function-name "$FN" \
                --statement-id logs-invoke-iris-layout --action lambda:InvokeFunction \
                --principal logs.amazonaws.com \
                --source-arn "arn:$PARTITION:logs:$REGION:$ACCOUNT:log-group:$PIPELINE_LOG_GROUP:*" \
                --source-account "$ACCOUNT" 2>&1); then
            grep -q ResourceConflictException <<<"$OUT" || { echo "$OUT" >&2; exit 1; }
        fi
        # Same filter name overwrites, so re-running just updates the pattern.
        for i in 1 2 3 4 5; do   # the new permission can take a moment to apply
            if "${AWS[@]}" logs put-subscription-filter --log-group-name "$PIPELINE_LOG_GROUP" \
                --filter-name "$FILTER_NAME" --filter-pattern "$FILTER_PATTERN" \
                --destination-arn "$FN_ARN"; then break; fi
            (( i == 5 )) && { echo "Could not add the subscription filter" >&2; exit 1; }
            echo "   retrying in 10s"; sleep 10
        done
        "${AWS[@]}" logs describe-subscription-filters --log-group-name "$PIPELINE_LOG_GROUP" \
            --query 'subscriptionFilters[].[filterName,destinationArn]' --output table
    fi
fi

echo
echo "Done. Results for each article will appear in s3://$BUCKET/$OUTPUT_PREFIX"
echo "Logs: aws logs tail /aws/lambda/$FN --follow --region $REGION"
