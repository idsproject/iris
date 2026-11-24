#!/usr/bin/env bash
# Remove the iris-layout trigger, log subscription, function and role. Leaves
# the ASU pipeline, its triggers, its log group and any files already written
# under iris/ untouched.
#
#   BUCKET=<pdf2html bucket name> ./remove.sh
set -euo pipefail
BUCKET="${BUCKET:?set BUCKET=<pdf2html bucket name>}"
FN="${FN:-iris-layout}"
ROLE_NAME="${ROLE_NAME:-iris-layout-role}"
PROFILE="${PROFILE:-}"
PIPELINE_LOG_GROUP="${PIPELINE_LOG_GROUP:-/aws/lambda/Pdf2HtmlPipeline}"
TRIGGER_ID="iris-layout-trigger"
FILTER_NAME="iris-layout-pipeline-failures"
AWS=(aws)
[[ -n "$PROFILE" ]] && AWS+=(--profile "$PROFILE")
[[ -n "${REGION:-}" ]] && AWS+=(--region "$REGION")
WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
PARTITION=$("${AWS[@]}" sts get-caller-identity --query Arn --output text | cut -d: -f2)

"${AWS[@]}" s3api get-bucket-notification-configuration --bucket "$BUCKET" > "$WORK/n.json"
python3 - "$WORK/n.json" "$TRIGGER_ID" > "$WORK/n2.json" <<'PY'
import json, sys
raw = open(sys.argv[1]).read().strip()
cfg = json.loads(raw) if raw else {}
cfg.pop("ResponseMetadata", None)
lam = [c for c in cfg.get("LambdaFunctionConfigurations", []) if c.get("Id") != sys.argv[2]]
if lam: cfg["LambdaFunctionConfigurations"] = lam
else: cfg.pop("LambdaFunctionConfigurations", None)
print(json.dumps(cfg))
PY
"${AWS[@]}" s3api put-bucket-notification-configuration --bucket "$BUCKET" \
    --notification-configuration "file://$WORK/n2.json"
echo "trigger removed"
"${AWS[@]}" logs delete-subscription-filter --log-group-name "$PIPELINE_LOG_GROUP" \
    --filter-name "$FILTER_NAME" 2>/dev/null && echo "log subscription removed" \
    || echo "no log subscription on $PIPELINE_LOG_GROUP"
"${AWS[@]}" lambda delete-function --function-name "$FN" && echo "function deleted" || true
"${AWS[@]}" iam delete-role-policy --role-name "$ROLE_NAME" --policy-name iris-layout-access 2>/dev/null || true
"${AWS[@]}" iam detach-role-policy --role-name "$ROLE_NAME" \
    --policy-arn "arn:$PARTITION:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole" 2>/dev/null || true
"${AWS[@]}" iam delete-role --role-name "$ROLE_NAME" 2>/dev/null && echo "role deleted" || true
