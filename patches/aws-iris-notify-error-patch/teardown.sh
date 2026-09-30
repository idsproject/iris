#!/usr/bin/env bash
# Removes everything setup.sh created. Leaves your log groups untouched.
#
# Usage: ./teardown.sh --profile my-admin-profile
set -uo pipefail

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
ROLE_NAME="${FUNCTION_NAME}-role"
FILTER_NAME="failure-notifier"

# ---------- Log groups (from log-groups.txt) ----------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_GROUPS=()
while IFS= read -r line || [[ -n "$line" ]]; do
  line="${line%%#*}"                     # drop comments
  line="$(printf '%s' "$line" | tr -d ',"'"'"' \t\r')"  # drop commas, quotes, whitespace
  [[ -z "$line" ]] && continue
  if [[ ! "$line" =~ ^[-._/#A-Za-z0-9]+$ ]]; then
    echo "Invalid log group name in log-groups.txt: '$line'" >&2
    exit 1
  fi
  LOG_GROUPS+=("$line")
done < "$SCRIPT_DIR/log-groups.txt"
if [[ ${#LOG_GROUPS[@]} -eq 0 ]]; then
  echo "No log groups listed in log-groups.txt" >&2
  exit 1
fi
# -------------------------------------------------------

for LG in "${LOG_GROUPS[@]}"; do
  aws logs delete-subscription-filter --region "$REGION" \
    --log-group-name "$LG" --filter-name "$FILTER_NAME" \
    && echo "Removed filter from $LG"
done

aws lambda delete-function --region "$REGION" --function-name "$FUNCTION_NAME" \
  && echo "Deleted function"

for POLICY in read-iris-notification-secret decrypt-iris-notification-secret; do
  aws iam delete-role-policy --role-name "$ROLE_NAME" --policy-name "$POLICY" 2>/dev/null
done
aws iam detach-role-policy --role-name "$ROLE_NAME" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam delete-role --role-name "$ROLE_NAME" && echo "Deleted role"
