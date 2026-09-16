#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C

FN="${FN:?set FN=your-function-name}"
PROFILE="${PROFILE:-}"              # empty = default profile / AWS_PROFILE env
S3_BUCKET="${S3_BUCKET:-}"          # optional, needed only if zip is large
HOOKS_ONLY="${HOOKS_ONLY:-}"        # set to 1 to redeploy only iris-hook.py
TARGET=title_generator.py
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

WORK=$(mktemp -d)
cleanup() {
    local rc=$?
    if (( rc != 0 )); then
        echo "Failed (rc=$rc). Left workdir for inspection: $WORK" >&2
    else
        rm -rf "$WORK"
    fi
}
trap cleanup EXIT

AWS=(aws)
if [[ -n "$PROFILE" ]]; then
    AWS+=(--profile "$PROFILE")
fi

if command -v sha256sum >/dev/null 2>&1; then
    SHA=(sha256sum)
else
    SHA=(shasum -a 256)
fi

echo "Function: $FN    Profile: ${PROFILE:-<default>}"
"${AWS[@]}" sts get-caller-identity --query Arn --output text

deploy_zip() {
    local zipfile=$1 size
    size=$(wc -c < "$zipfile")
    if (( size > 40000000 )); then
        if [[ -z "$S3_BUCKET" ]]; then
            echo "Zip is ${size}B; set S3_BUCKET for large uploads." >&2
            return 1
        fi
        local key="lambda-patched/$FN-$(date +%s).zip"
        "${AWS[@]}" s3 cp "$zipfile" "s3://$S3_BUCKET/$key"
        "${AWS[@]}" lambda update-function-code --function-name "$FN" \
            --s3-bucket "$S3_BUCKET" --s3-key "$key" --publish
    else
        "${AWS[@]}" lambda update-function-code --function-name "$FN" \
            --zip-file "fileb://$zipfile" --publish
    fi
    "${AWS[@]}" lambda wait function-updated-v2 --function-name "$FN"
}

# 0. Don't race an in-flight deploy
"${AWS[@]}" lambda wait function-updated-v2 --function-name "$FN"

# 1. Pull what's deployed
URL=$("${AWS[@]}" lambda get-function --function-name "$FN" \
      --query 'Code.Location' --output text)
curl -fsS -o "$WORK/deployed.zip" "$URL"
unzip -q "$WORK/deployed.zip" -d "$WORK/build"

if [[ ! -f "$WORK/build/$TARGET" ]]; then
    echo "ERROR: $TARGET not in bundle. Renamed upstream?" >&2
    exit 1
fi

ALREADY_PATCHED=0
if grep -q 'PATCH-MARKER' "$WORK/build/$TARGET"; then
    ALREADY_PATCHED=1
fi

# 1b. Hooks-only redeploy: swap iris-hook.py, leave the patch alone
if [[ -n "$HOOKS_ONLY" ]]; then
    if (( ALREADY_PATCHED == 0 )); then
        echo "ERROR: bundle isn't patched; run without HOOKS_ONLY first." >&2
        exit 1
    fi
    cp "$REPO/src/iris-hook.py" "$WORK/build/"
    chmod -R u+rwX,go+rX "$WORK/build"
    (cd "$WORK/build" && zip -qry "$WORK/hooks.zip" .)
    deploy_zip "$WORK/hooks.zip"
    echo "iris-hook.py redeployed."
    exit 0
fi

if (( ALREADY_PATCHED == 1 )); then
    echo "Already patched. (Use HOOKS_ONLY=1 to redeploy iris-hook.py.)"
    exit 0
fi

# 2. Manifest of the pristine bundle
(cd "$WORK/build" && find . -type f -not -path '*/__pycache__/*' -print0 \
    | sort -z | xargs -0 "${SHA[@]}") > "$WORK/manifest.txt"

if [[ -f "$REPO/manifest.txt" ]] && \
   ! diff -q "$REPO/manifest.txt" "$WORK/manifest.txt" >/dev/null; then
    echo "=== Bundle changed since last run ==="
    diff -u "$REPO/manifest.txt" "$WORK/manifest.txt" | grep -E '^[+-][0-9a-f]' || true
    echo
fi

# 3. Bootstrap on first run
if [[ ! -f "$REPO/baseline/$TARGET" ]]; then
    mkdir -p "$REPO/baseline"
    cp "$WORK/build/$TARGET" "$REPO/baseline/$TARGET"
    cp "$WORK/manifest.txt" "$REPO/manifest.txt"
    echo "Bootstrapped baseline + manifest. Generate patches/hook.diff, commit, re-run."
    exit 0
fi

# 4. Did the file we patch change?
if ! diff -q "$REPO/baseline/$TARGET" "$WORK/build/$TARGET" >/dev/null; then
    echo "UPSTREAM CHANGED in $TARGET — review before deploying:"
    diff -u "$REPO/baseline/$TARGET" "$WORK/build/$TARGET" || true
    exit 1
fi

# 5. Apply
cp "$REPO/src/iris-hook.py" "$WORK/build/"
patch -p1 -d "$WORK/build" --forward --fuzz=0 < "$REPO/iris-notify.patch"

# 6. Repackage and deploy
chmod -R u+rwX,go+rX "$WORK/build"
(cd "$WORK/build" && zip -qry "$WORK/patched.zip" .)
deploy_zip "$WORK/patched.zip"

# 7. Record manifest only after a successful deploy
cp "$WORK/manifest.txt" "$REPO/manifest.txt"
echo "Patched and deployed. Review 'git diff manifest.txt'."

