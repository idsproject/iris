# aws-iris-layout-notify

One Lambda (`iris-layout`) next to the ASU **PDF-to-HTML** pipeline (`Pdf2HtmlPipeline`) that

1. builds the files IRIS needs once the pipeline finishes an article, and
2. tells IRIS (`POST /v1/notify`) when the layout succeeds and when either the
   pipeline or the layout fails.

| File (in the pdf2html bucket) | Purpose |
|---|---|
| `iris/<name>_print.html` | PrinceXML source. Prince turns it into a tagged PDF/UA-1 PDF with no command-line options (tagging and the profile are set inside the file). |
| `iris/<name>_layout.html` | Page-faithful accessible HTML for browsers (axe-clean, reflows on phones). |
| `iris/<name>_review.json` | What still needs a person: figure alt text, any table cells that could not be placed. |

It is a separate function with its own trigger and log subscription, so ASU
redeploys do not overwrite it, and it does not modify any ASU code.

```
IRIS ──upload──▶ uploads/<file>.pdf
                     │  (S3 trigger, ASU)
                     ▼
              Pdf2HtmlPipeline ──▶ output/<name>.zip            (BDA result.json, remediated.html)
                  │             └─▶ remediated/final_<name>.zip  (written last)
                  │                         │  (S3 trigger, this package)
                  │ failure lines           ▼
                  │ (log subscription)  iris-layout ──▶ iris/<name>_print.html, _layout.html, _review.json
                  └────────────────────▶    │
                                            ├─▶ POST /v1/notify  "layout complete"    (success)
                                            └─▶ POST /v1/notify  "remediation error"  (pipeline or layout failed)
```

`<name>` is the pipeline's sanitized file name (spaces become underscores, etc.).
IRIS always gets back `<file>.pdf`, the name it uploaded.

## Where this came from

This replaces three of the earlier packages in `patches/`:

| Package | What happened to it |
|---|---|
| `aws-iris-layout` | Base for this package (handler, deploy/remove, fonts, pinned requirements). Its `src/iris_layout.py` is used unchanged. |
| `aws-iris-notify-error-patch` | Superseded. It parsed the PDF-to-PDF pipeline's `File: x, Status: Failed` logs; the failure notifier here reads `Pdf2HtmlPipeline`'s log instead. |
| `aws-iris-notify-success-patch` | Superseded. It patched the PDF-to-PDF title generator to send `remediation complete`; here the layout sends `layout complete`. |

Changes from `aws-iris-layout`:

* Notifications use IRIS's `NotifyRequest` shape: `file` is the original upload
  name (it was `<name>_print.html`, which IRIS can't match to a request), and
  extra fields go in `payload`.
* A failed layout sends `remediation error`, which IRIS already handles (it was
  `layout failed`, which IRIS rejects).
* `Pdf2HtmlPipeline` failures now reach IRIS too (see below).
* The layout stops 45 s before the Lambda timeout so a stuck article is still
  reported.
* `deploy.sh` grants the secret by its exact ARN, adds `kms:Decrypt` when the
  secret uses a customer-managed key, and checks the log filter before using it.

## Contents

```
deploy.sh                  build + deploy/update (safe to re-run)
remove.sh                  remove the trigger, log subscription, function and role
requirements.txt           pinned libraries (numpy, fontTools, BeautifulSoup)
requirements-nodeps.txt    PyMuPDF 2.1, installed without its optional ML packages
src/lambda_function.py     handler: S3 events (layout) and log events (pipeline failures)
src/pipeline_failures.py   finds the failed article in Pdf2HtmlPipeline's log
src/iris_notify.py         IRIS notification
src/iris_layout.py         the layout/remediation script
src/fonts/                 Liberation fonts (SIL OFL 1.1, license included)
test/event.json            sample S3 event for a manual layout run
test/pipeline-failure-event.py   builds a sample pipeline-failure log event
test/test_handler.py       offline tests (standard library only)
patchenv/                  Python virtualenv deploy.sh builds with (not in git; created if missing)
```

## Prerequisites

* AWS CloudShell (or any shell with AWS CLI v2, `python3` with the `venv` module, and
  `zip`) in the account and region where the pdf2html stack is deployed. `deploy.sh`
  runs its Python, including the `pip` that builds the package, from the virtualenv
  `patchenv/` next to it, and creates it on the first run. On Debian/Ubuntu the
  `venv` module is the `python3-venv` package. To use a different virtualenv, set `VENV=<path>`.
* The pdf2html bucket name. Either of these prints it:
  ```
  aws lambda get-function-configuration --function-name Pdf2HtmlPipeline \
      --query Environment.Variables.BDA_S3_BUCKET --output text
  aws cloudformation describe-stacks --query "Stacks[].Outputs[?OutputKey=='BucketNameOutput'].OutputValue" --output text
  ```
* The IRIS notification secret `/myapp/iris-notification` (`{"url": ..., "api_key": ...}`),
  the same one the earlier notify patches used. Without it, deploy with
  `NOTIFY_IRIS=false`; files are still produced.
* `Pdf2HtmlPipeline` has run at least once, so its log group exists. If not,
  `deploy.sh` skips the log subscription with a warning; re-run it later.

## Install

```
cd aws-iris-layout-notify
BUCKET=<pdf2html bucket> ./deploy.sh
```

The script:

1. Checks the secret exists and tests the log filter pattern against sample
   pipeline lines (`aws logs test-metric-filter`).
2. Builds the package for Lambda (Python 3.13, x86_64) with `patchenv/`'s pip and checks
   it fits the 250 MB limit.
3. Creates role `iris-layout-role`. It can read `uploads/`, `output/`, `remediated/`,
   write only `iris/`, read the IRIS secret, and search `Pdf2HtmlPipeline`'s log.
4. Creates or updates function `iris-layout` (2 GB memory, 5 min timeout, 2 GB /tmp,
   no automatic retries).
5. Adds the S3 trigger `remediated/final_*.zip` **alongside** the bucket's existing
   triggers (the ASU `uploads/` trigger is kept). A backup of the previous trigger
   configuration is saved next to the script as `notification-backup-*.json`.
6. Adds subscription filter `iris-layout-pipeline-failures` on
   `/aws/lambda/Pdf2HtmlPipeline`, sending only its failure lines to `iris-layout`.

Optional settings: `FN`, `ROLE_NAME`, `PROFILE`, `REGION`, `OUTPUT_PREFIX` (default `iris/`),
`IRIS_SECRET_NAME`, `NOTIFY_IRIS`, `WATCH_PIPELINE` (default `true`), `PIPELINE_LOG_GROUP`,
`MEMORY`, `TIMEOUT` (at least 120), `TMP_MB`, `VENV` (default `./patchenv`).

**After any ASU redeploy**, re-run `./deploy.sh`. It is idempotent, and it
restores the S3 trigger if the redeploy removed it.

## What IRIS receives

`POST <url>` with header `Authorization: <api_key>`.

Layout succeeded:

```json
{
  "sender": "aws-iris-layout",
  "status": "success",
  "file": "<file>.pdf",
  "message": "layout complete",
  "payload": {
    "bucket": "<pdf2html bucket>",
    "key": "iris/<name>_print.html",
    "layout_key": "iris/<name>_layout.html",
    "review_key": "iris/<name>_review.json",
    "source_key": "uploads/<file>.pdf",
    "figures_needing_alt_text_review": 2,
    "table_cells_unplaced": 0
  }
}
```

The pipeline or the layout failed:

```json
{
  "sender": "aws-iris-layout",
  "status": "error",
  "file": "<file>.pdf",
  "message": "remediation error",
  "payload": {"step": "pdf2html", "source_key": "uploads/<file>.pdf", "error": "...",
              "log_group": "...", "log_stream": "...", "timestamp": 1764000000000, "log_line": "..."}
}
```

For a layout failure `step` is `iris-layout` and the payload has `bucket`,
`trigger_key`, `source_key` and `error`. If the upload can't be found, `file`
falls back to `<name>.pdf`.

### How pipeline failures are found

`Pdf2HtmlPipeline` catches its own errors and returns normally, so Lambda never
records a failed invocation and on-failure destinations never fire. Its log is
the only signal. The subscription sends these lines to `iris-layout`:

| Line | File taken from |
|---|---|
| `[ERROR] Failed to download s3://<bucket>/<key>: ...` | the line |
| `[ERROR] Processing <key> failed: ...` | the line |
| `[ERROR] Creating or uploading zip failed: ...` | the log stream |
| `[ERROR] Unhandled exception: ...` | the log stream |
| `Task timed out after ...` (Lambda) | the log stream |
| `Runtime exited with error ...` (Lambda, e.g. out of memory) | the log stream |

"The log stream" means: the pipeline prints `[INFO] Processing s3://<bucket>/<key>`
at the start of each run, and a log stream runs one invocation at a time, so
`iris-layout` searches the same stream for the last such line after the last
`START RequestId`. Errors that Python's `logging` module writes (`[ERROR]`
followed by a tab) are ignored, so a library logging an error it recovered from
does not mark the article failed.

These patterns match `pdf2html/lambda_function.py` on ASU's main branch
(github.com/ASUCICREPO/PDF_Accessibility, checked November 2026). If the deployed
image is older or newer, or after an ASU update, compare its `print(f"[ERROR] ...")` lines with
`FAILURE_LINES` in `src/pipeline_failures.py`.

## IRIS changes needed (Go, not part of this package)

1. Send HTML-route articles to the **pdf2html** bucket as `uploads/<file>.pdf`
   (no subfolder; the pipeline skips those). Today `UploadArticle` writes to
   `PDF_BUCKET_NAME`/`pdf/`, which feeds the PDF-to-PDF pipeline.
2. Add `layout complete` to the `NotifyRequest.message` enum in
   `openapi/open-api.yaml`, regenerate `api/api.gen.go`, and handle it in
   `Notify`: download `payload.bucket`/`payload.key`, convert it with Prince,
   and write the PDF to `$DOWNLOAD_DIR/<file>`, so `/v1/status` reports it
   done. Until then IRIS's request validation answers HTTP 400; the Lambda
   logs that once, without retrying, and the files stay in S3.
3. `remediation error` needs no change. IRIS writes `<file>.error` and
   `/v1/status` reports `remediation_error`.

## Test

Offline (no AWS, no IRIS):

```
python3 -m unittest discover -s test -v
```

Run an article through the pipeline as usual (upload to `uploads/`), then:

```
aws logs tail /aws/lambda/iris-layout --follow
aws s3 ls s3://<bucket>/iris/
```

To re-run the layout on an article the pipeline already processed, edit
`test/event.json` (bucket and `remediated/final_<name>.zip`) and:

```
aws lambda invoke --function-name iris-layout --cli-binary-format raw-in-base64-out \
    --payload file://test/event.json out.json && cat out.json
```

A finished article whose output already exists is skipped. To rebuild anyway,
set `FORCE_REBUILD=true` in the function's environment temporarily.

To send a pipeline failure through the function (this sends IRIS a real
`remediation error` for that file):

```
python3 test/pipeline-failure-event.py "uploads/<file>.pdf" > failure.json
aws lambda invoke --function-name iris-layout --cli-binary-format raw-in-base64-out \
    --payload file://failure.json out.json && cat out.json      # {"failures": 1, "sent": 1}
```

Typical layout run: 3 to 6 seconds for an 11-page article.

## Limits

* **Pipeline retries.** `Pdf2HtmlPipeline` timeouts and crashes count as failed
  invocations, and ASU's stack keeps Lambda's default of 2 retries. IRIS can get
  `remediation error` and later `layout complete` for the same file. The
  pipeline's own `[ERROR]` lines are not retried.
* **Failures with no file.** If the pipeline fails before printing its
  `Processing` line (for example a container that can't start), nothing ties
  the failure to an article. `iris-layout` logs a `[WARNING]` and sends nothing.
* **iris-layout out of memory.** Its own timeouts are caught; an out-of-memory
  kill is not reported to IRIS (it does not watch its own log, which could
  loop). It shows in Lambda's error metrics.
* **IRIS unreachable.** Each notification is tried 3 times (up to about 35 s),
  then logged as a `[WARNING]` and dropped.
* **Log format.** The matching expects the pipeline's log in Lambda's default
  text format, not JSON.
* CloudWatch allows 2 subscription filters per log group; this uses one.

## Remove

```
BUCKET=<pdf2html bucket> ./remove.sh
```

Removes only this trigger, log subscription, function and role. Files under `iris/` are left in place.
