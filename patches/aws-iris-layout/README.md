# aws-iris-layout

A Lambda that runs after the ASU **PDF-to-HTML** pipeline (`Pdf2HtmlPipeline`)
finishes an article, and builds the files IRIS needs:

| File (in the pdf2html bucket) | Purpose |
|---|---|
| `iris/<name>_print.html` | PrinceXML source. Prince turns it into a tagged PDF/UA-1 PDF with no command-line options (tagging and the profile are set inside the file). |
| `iris/<name>_layout.html` | Page-faithful accessible HTML for browsers (axe-clean, reflows on phones). |
| `iris/<name>_review.json` | What still needs a person: figure alt text, any table cells that could not be placed. |

It is a separate function with its own trigger, so ASU redeploys do not
overwrite it, and it does not modify any ASU code.

```
IRIS ──upload──▶ uploads/<file>.pdf
                     │  (S3 trigger, ASU)
                     ▼
              Pdf2HtmlPipeline ──▶ output/<name>.zip            (BDA result.json, remediated.html)
                                └─▶ remediated/final_<name>.zip  (written last)
                                          │  (S3 trigger, this package)
                                          ▼
                                     iris-layout ──▶ iris/<name>_print.html, _layout.html, _review.json
                                          │
                                          └─▶ POST IRIS /v1/notify  {"message": "layout complete", ...}
```

`<name>` is the pipeline's sanitized file name (spaces become underscores, etc.).

## Contents

```
deploy.sh            build + deploy/update (safe to re-run)
remove.sh            remove the trigger, function and role
requirements.txt         pinned libraries (numpy, fontTools, BeautifulSoup)
requirements-nodeps.txt  PyMuPDF 2.1, installed without its optional ML packages
src/lambda_function.py   handler
src/iris_notify.py       IRIS notification (same secret/payload shape as iris_hook.py)
src/iris_layout.py       the layout/remediation script
src/fonts/               Liberation fonts (SIL OFL 1.1, license included)
test/event.json          sample event for a manual run
```

## Prerequisites

* AWS CloudShell (or any shell with AWS CLI v2, `python3`, `pip`, `zip`) in the
  account and region where the pdf2html stack is deployed.
* The pdf2html bucket name. Either of these prints it:
  ```
  aws lambda get-function-configuration --function-name Pdf2HtmlPipeline \
      --query Environment.Variables.BDA_S3_BUCKET --output text
  aws cloudformation describe-stacks --query "Stacks[].Outputs[?OutputKey=='BucketNameOutput'].OutputValue" --output text
  ```
* The IRIS notification secret `/myapp/iris-notification` (`{"url": ..., "api_key": ...}`),
  already created for the title-generator notify patch. Without it, deploy with
  `NOTIFY_IRIS=false`; files are still produced.

## Install

```
unzip aws-iris-layout.zip && cd aws-iris-layout
BUCKET=<pdf2html bucket> ./deploy.sh
```

The script:

1. Builds the package for Lambda (Python 3.13, x86_64) and checks it fits the 250 MB limit.
2. Creates role `iris-layout-role`. It can read `uploads/`, `output/`, `remediated/`,
   write only `iris/`, and read the IRIS secret.
3. Creates or updates function `iris-layout` (2 GB memory, 5 min timeout, 2 GB /tmp,
   no automatic retries).
4. Adds the S3 trigger `remediated/final_*.zip` **alongside** the bucket's existing
   triggers (the ASU `uploads/` trigger is kept). A backup of the previous trigger
   configuration is saved next to the script as `notification-backup-*.json`.

Optional settings: `FN`, `ROLE_NAME`, `PROFILE`, `REGION`, `OUTPUT_PREFIX` (default `iris/`),
`IRIS_SECRET_NAME`, `NOTIFY_IRIS`, `MEMORY`, `TIMEOUT`, `TMP_MB`.

**After any ASU redeploy**, re-run `./deploy.sh`. It is idempotent, and it
restores the trigger if the redeploy removed it.

## Test

Run an article through the pipeline as usual (upload to `uploads/`), then:

```
aws logs tail /aws/lambda/iris-layout --follow
aws s3 ls s3://<bucket>/iris/
```

To re-run on an article the pipeline already processed, edit `test/event.json`
(bucket and `remediated/final_<name>.zip`) and:

```
aws lambda invoke --function-name iris-layout --cli-binary-format raw-in-base64-out \
    --payload file://test/event.json out.json && cat out.json
```

A finished article whose output already exists is skipped. To rebuild anyway,
set `FORCE_REBUILD=true` in the function's environment temporarily.

Typical run: 3 to 6 seconds for an 11-page article.

## What IRIS receives

`POST <url>` with header `Authorization: <api_key>`:

```json
{
  "sender": "aws-iris-layout",
  "status": "success",
  "message": "layout complete",
  "file": "<name>_print.html",
  "bucket": "<pdf2html bucket>",
  "key": "iris/<name>_print.html",
  "layout_key": "iris/<name>_layout.html",
  "review_key": "iris/<name>_review.json",
  "source_pdf_key": "uploads/<original>.pdf",
  "figures_needing_alt_text_review": 2,
  "table_cells_unplaced": 0
}
```

On failure: `"status": "error", "message": "layout failed"` with `trigger_key` and `error`.

**IRIS changes needed** (Go, not part of this package):

1. Send HTML-route articles to the **pdf2html** bucket's `uploads/` prefix. Today
   `UploadArticle` writes to `PDF_BUCKET_NAME`/`pdf/`, which feeds the PDF-to-PDF pipeline.
2. Handle `"layout complete"` in `HandleNotify`: download `bucket`/`key`, convert it
   with Prince, deliver the PDF, then delete or keep the `iris/` files.
   Until this exists, IRIS answers "Unknown message" (HTTP 400). The Lambda logs that
   once, without retrying, and the files stay in S3.
3. Handle `"layout failed"` (log it, fall back or flag the request).

## Remove

```
BUCKET=<pdf2html bucket> ./remove.sh
```

Removes only this trigger, function and role. Files under `iris/` are left in place.
