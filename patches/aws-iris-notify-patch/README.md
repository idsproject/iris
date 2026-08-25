# Adding IRIS Notification to the Title Generator Lambda

This guide walks through modifying the PDF-to-PDF remediation pipeline's
Title Generator Lambda (`lambda/title-generator-lambda`) so that it notifies
the IRIS API once a remediated PDF has been saved to the `result/` folder.
This is the final step in the [ASUCICREPO/PDF_Accessibility](https://github.com/ASUCICREPO/PDF_Accessibility)
Step Functions workflow, so it is the natural point to signal that a file is
ready for IRIS to pick up.

This change is deployed **without** forking or maintaining a full clone of
the upstream ASU repo, so that future upstream bug fixes can still be pulled
in cleanly. Instead, only the three files needed to rebuild this one Lambda
are maintained here:

[idsproject/iris — `iris-title-generator-build`](https://github.com/idsproject/iris/tree/main/iris-title-generator-build)

That folder contains the patched `title_generator.py`, along with the
unmodified `requirements.txt` and `Dockerfile` from upstream, and is the
source of truth for rebuilding and redeploying this Lambda.

## What this adds

- A call to Secrets Manager to retrieve an IRIS notification URL and API key
- A `notify_iris_file_ready()` function that POSTs a JSON payload to IRIS
  after the remediated PDF is uploaded to `result/COMPLIANT_{filename}.pdf`
- Retry with exponential backoff, reusing the pattern already used elsewhere
  in this Lambda
- Non-fatal failure handling: if IRIS is unreachable, the remediation
  workflow still completes successfully; only the notification is skipped

## Prerequisites

- AWS CLI configured with credentials for the target account
- Docker (AWS CloudShell has both preinstalled and preauthenticated, and is
  the easiest place to run this without a local setup)
- The function name of your deployed Title Generator Lambda, e.g.:
  ```
  PDFAccessibility-BedrockTitleGeneratorLambdaC13D08-oXbVoIV19PXT
  ```
- The IAM role name attached to that Lambda (see Step 2)

## Step 1: Create the IRIS notification secret

Store the IRIS endpoint URL and API key in AWS Secrets Manager rather than
in code or a config file, so the credential never lands in source control or
the deployed package.

```bash
aws secretsmanager create-secret \
  --name "/myapp/iris-notification" \
  --secret-string '{"url":"<IRIS_NOTIFICATION_URL>","api_key":"<IRIS_API_KEY>"}'
```

Replace `<IRIS_NOTIFICATION_URL>` and `<IRIS_API_KEY>` with your real values.
The `/myapp/*` prefix matches the convention already used for the Adobe API
credentials in this project.

Save the `ARN` returned by this command — you'll need it in Step 2.

To update the URL or key later without redeploying the Lambda:

```bash
aws secretsmanager put-secret-value \
  --secret-id "/myapp/iris-notification" \
  --secret-string '{"url":"<new-url>","api_key":"<new-key>"}'
```

The Lambda re-reads the secret on each cold start.

## Step 2: Grant the Lambda permission to read the secret

Find the Lambda's execution role:

```bash
aws lambda get-function-configuration \
  --function-name "<YOUR_TITLE_GENERATOR_FUNCTION_NAME>" \
  --query 'Role' --output text
```

This returns a role ARN, e.g.
`arn:aws:iam::<account-id>:role/PDFAccessibility-BedrockTitleGeneratorLambdaService-<suffix>`.
The role name is everything after `role/`.

Check whether the role already has Secrets Manager access:

```bash
aws iam list-role-policies --role-name "<ROLE_NAME>"
aws iam get-role-policy --role-name "<ROLE_NAME>" --policy-name "<POLICY_NAME>"
```

If no `secretsmanager:GetSecretValue` statement covers your new secret, add
a **new, separate** inline policy (don't edit the CDK-managed default
policy — CDK will overwrite manual edits to it on the next `cdk deploy`):

```bash
aws iam put-role-policy \
  --role-name "<ROLE_NAME>" \
  --policy-name "IrisSecretAccess" \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Action": "secretsmanager:GetSecretValue",
        "Resource": "<SECRET_ARN_FROM_STEP_1>-*"
      }
    ]
  }'
```

Verify:

```bash
aws iam get-role-policy --role-name "<ROLE_NAME>" --policy-name "IrisSecretAccess"
```

## Step 3: Update `title_generator.py`

Apply the following changes to `title_generator.py`. The full file with
these changes already applied is maintained at
[`iris-title-generator-build/title_generator.py`](https://github.com/idsproject/iris/tree/main/iris-title-generator-build)
in this repository — pull from there directly rather than reapplying the
diff by hand each time.

**1. Add imports and the secret name constant near the top of the file:**

```python
import urllib.request
import urllib.error

# Name/path of the Secrets Manager secret holding the IRIS endpoint + API key.
# Secret should be a JSON object: {"url": "...", "api_key": "..."}
IRIS_SECRET_NAME = "/myapp/iris-notification"

_iris_config_cache = None  # cached for the lifetime of the Lambda execution environment
```

**2. Add three new functions** (placed after `save_to_s3` in the reference
file): `_get_iris_config()`, `_post_to_iris()`, and
`notify_iris_file_ready()`. These reuse the existing
`exponential_backoff_retry()` helper already defined in this file, so no new
retry logic is introduced. See the reference file for the full
implementation.

**3. Call the notification function in `lambda_handler`**, immediately
after the existing `save_to_s3(...)` call succeeds:

```python
        try:
            save_path = save_to_s3(local_path, file_info['bucket'], file_name)
            print(f"(lambda_handler | Saved file to S3 at: {save_path})")
        except Exception as e:
            print(f"(lambda_handler | Failed to save file to S3: {e})")
            return {
                "statusCode": 500,
                "body": {
                    "error": "Failed to save file to S3.",
                    "details": f"{file_name} - {str(e)}"
                }
            }

        # Notify IRIS that the remediated file is ready for download.
        # Non-fatal: an IRIS notification failure does not fail the workflow.
        notify_iris_file_ready(file_info['bucket'], save_path, file_name)

        return {
```

No other part of `title_generator.py` is modified.

**Note on the auth header:** the reference implementation sends the API key
as `Authorization: Bearer <api_key>`. If your IRIS deployment expects a
different header (e.g. `X-API-Key`), update the `headers` dict in
`_post_to_iris()` accordingly.

## Step 4: Get the build files

All three files needed to rebuild the Lambda package —
`title_generator.py` (already patched), `requirements.txt`, and
`Dockerfile` — are kept together in this repository:

[idsproject/iris/iris-title-generator-build](https://github.com/idsproject/iris/tree/main/iris-title-generator-build)

Clone just this repository (not the upstream ASU repo) to pull them:

```bash
git clone https://github.com/idsproject/iris.git
cd iris/iris-title-generator-build
```

Since this is our own repository, it's safe to clone in full each time you
need to rebuild — there's no upstream-tracking conflict to worry about here.
If you've made further local edits to `title_generator.py` that haven't
been pushed yet, make sure they're committed to this repo before rebuilding
so the deployed package and the repo stay in sync.

## Step 5: Build the Lambda package

This Lambda is deployed as a **zip package**, not a container image — the
Dockerfile is used only to build dependencies (PyMuPDF has compiled
extensions that must match Amazon Linux, not your local OS) inside a
Lambda-compatible environment. Docker builds the package; it is not what
gets deployed.

```bash
docker build -t title-gen-build -f Dockerfile .
docker create --name temp-extract title-gen-build
docker cp temp-extract:/asset ./asset-output
docker rm temp-extract
cd asset-output
zip -r ../title_generator.zip .
cd ..
```

## Step 6: (Recommended) Back up the current deployed package

Before overwriting the live Lambda, save a rollback copy:

```bash
aws lambda get-function \
  --function-name "<YOUR_TITLE_GENERATOR_FUNCTION_NAME>" \
  --query 'Code.Location' --output text
```

This returns a presigned S3 URL — download it with `curl` and keep it
somewhere safe.

## Step 7: Deploy

```bash
aws lambda update-function-code \
  --function-name "<YOUR_TITLE_GENERATOR_FUNCTION_NAME>" \
  --zip-file fileb://title_generator.zip
```

Verify the update landed:

```bash
aws lambda get-function \
  --function-name "<YOUR_TITLE_GENERATOR_FUNCTION_NAME>" \
  --query 'Configuration.LastModified' --output text
```

## Step 8: Test

Upload a small test PDF to the `pdf/` folder of the `pdfaccessibility-*`
bucket to trigger the workflow, then tail the Title Generator's logs:

```bash
aws logs tail /aws/lambda/<YOUR_TITLE_GENERATOR_FUNCTION_NAME> --follow
```

Look for one of:

- `IRIS notified successfully (status <code>)` — the call succeeded
- `IRIS notification failed after retries: <error>` — the call failed after
  3 attempts; check the IRIS URL/key in Secrets Manager and confirm network
  connectivity from the Lambda to the IRIS endpoint

Either outcome confirms the new code path is executing. A failure here does
not affect the PDF remediation result — the file will still appear in
`result/` regardless of whether IRIS was notified.

## Keeping this in sync with upstream

This change lives in [idsproject/iris/iris-title-generator-build](https://github.com/idsproject/iris/tree/main/iris-title-generator-build),
kept separate from a full clone of the upstream ASU repo so upstream bug
fixes can still be pulled in cleanly. When ASUCICREPO/PDF_Accessibility
ships a fix to `title_generator.py`, `requirements.txt`, or the
`Dockerfile`:

1. Pull the updated file(s) from
   [ASUCICREPO/PDF_Accessibility](https://github.com/ASUCICREPO/PDF_Accessibility/tree/main/lambda/title-generator-lambda)
2. Reapply the three IRIS-notification edits from Step 3 to the new
   `title_generator.py`
3. Commit the merged result back to
   [idsproject/iris/iris-title-generator-build](https://github.com/idsproject/iris/tree/main/iris-title-generator-build)
4. Rebuild and redeploy (Steps 5–7)

If your team ever runs `cdk deploy` from a full checkout of the upstream ASU
repo, note that it will rebuild the Lambda from its own Docker asset and
**overwrite this manual deployment**, removing the IRIS notification code.
Reapply this guide (starting from Step 4, pulling the current version from
`iris-title-generator-build`) after any such redeploy.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `AccessDeniedException` fetching the secret | IAM policy from Step 2 not attached, or scoped to the wrong ARN |
| IRIS notification always fails, PDF remediation still succeeds | Expected if Secrets Manager still has placeholder values, or if the Lambda has no network path to the IRIS endpoint (check VPC/NAT configuration if this Lambda is VPC-attached) |
| `ResourceNotFoundException` for the secret | Secret name in `IRIS_SECRET_NAME` doesn't match what was created in Step 1 |
| Lambda fails to import after redeploy | PyMuPDF dependency not built correctly — confirm the zip was built from `/asset` inside the Docker container, not a local `pip install` |
