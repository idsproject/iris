"""
iris-layout Lambda

Two jobs, picked by the shape of the event:

1. S3 event (layout). Runs after the ASU PDF-to-HTML pipeline
   (Pdf2HtmlPipeline) finishes an article and turns its output into the
   files IRIS needs:

    iris/<name>_print.html    PrinceXML source -> tagged PDF/UA PDF
    iris/<name>_layout.html   page-faithful accessible HTML for browsers
    iris/<name>_review.json   what needs a human (figure alt text, tables)

   Trigger: S3 ObjectCreated on the pdf2html bucket, prefix "remediated/final_",
   suffix ".zip". The pipeline writes that file last, after output/<name>.zip,
   so everything this function reads is already in place:

    uploads/<original name>.pdf         the article as uploaded
    output/<name>.zip                   BDA result.json + remediated_html/remediated.html
    remediated/final_<name>.zip         fallback source for remediated.html

   <name> is the pipeline's sanitized file name (spaces -> underscores etc.),
   reproduced here with the pipeline's own sanitize_filename().

   Notifies IRIS with "layout complete" when done, or "remediation error" if
   the layout fails or runs out of time.

2. CloudWatch Logs event (pipeline failure). A subscription filter on
   Pdf2HtmlPipeline's log group sends its failure lines here, and IRIS gets
   "remediation error" for the article. See pipeline_failures.py.

In both cases IRIS's `file` is the original upload name (<original name>.pdf).
"""
import base64
import contextlib
import gzip
import json
import os
import shutil
import signal
import tempfile
import time
import traceback
import urllib.parse
import zipfile

import boto3
from botocore.exceptions import ClientError

import iris_layout
import pipeline_failures
from iris_notify import notify_iris

TRIGGER_PREFIX = "remediated/final_"
OUTPUT_PREFIX = os.environ.get("OUTPUT_PREFIX", "iris/")
UPLOADS_PREFIX = os.environ.get("UPLOADS_PREFIX", "uploads/")
PIPELINE_OUTPUT_PREFIX = os.environ.get("PIPELINE_OUTPUT_PREFIX", "output/")
FORCE = os.environ.get("FORCE_REBUILD", "false").lower() == "true"
FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
# Stop the layout this long before the Lambda timeout, leaving time to tell
# IRIS (a notification can take ~35 s with retries).
TIME_MARGIN_SECONDS = 45

s3 = boto3.client("s3")


class LayoutTimeout(Exception):
    pass


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def sanitize_filename(filename):
    """Copied from the pdf2html pipeline (pdf2html/lambda_function.py) so
    our <name> matches the keys it writes."""
    sanitized = filename.replace(" ", "_")
    while "__" in sanitized:
        sanitized = sanitized.replace("__", "_")
    for char in ["#", "%", "&", "{", "}", "\\", "<", ">", "*", "?", "/", "$", "!", "'", '"', ":", "@", "+", "`", "|", "="]:
        sanitized = sanitized.replace(char, "_")
    sanitized = sanitized.strip("_")
    return sanitized or "document"


def head(bucket, key):
    try:
        return s3.head_object(Bucket=bucket, Key=key)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


def find_original_pdf(bucket, name):
    """uploads/<name>.pdf, or the upload whose sanitized name matches."""
    for ext in (".pdf", ".PDF"):
        key = f"{UPLOADS_PREFIX}{name}{ext}"
        if head(bucket, key):
            return key
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=UPLOADS_PREFIX, Delimiter="/"):
        for obj in page.get("Contents", []):
            fname = obj["Key"][len(UPLOADS_PREFIX):]
            if fname.lower().endswith(".pdf") and os.path.splitext(sanitize_filename(fname))[0] == name:
                return obj["Key"]
    raise FileNotFoundError(f"no upload in s3://{bucket}/{UPLOADS_PREFIX} matches '{name}'")


def pick_member(names, suffix, prefer):
    hits = [n for n in names if n.endswith(suffix) and not n.endswith("/")]
    preferred = [n for n in hits if prefer in n]
    return (preferred or hits or [None])[0]


def extract(zip_path, member, dest_dir, dest_name):
    out = os.path.join(dest_dir, dest_name)
    with zipfile.ZipFile(zip_path) as z, z.open(member) as src, open(out, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return out


def upload(local, bucket, key, content_type):
    s3.upload_file(local, bucket, key, ExtraArgs={"ContentType": content_type})


@contextlib.contextmanager
def time_limit(seconds):
    """Raise LayoutTimeout inside the block after `seconds` (None: no limit)."""
    if not seconds:
        yield
        return

    def on_alarm(signum, frame):
        raise LayoutTimeout(f"layout stopped after {seconds} s, before the Lambda timeout")

    previous = signal.signal(signal.SIGALRM, on_alarm)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


# --------------------------------------------------------------------------
# main work
# --------------------------------------------------------------------------

def process(bucket, key, seconds=None):
    if not (key.startswith(TRIGGER_PREFIX) and key.lower().endswith(".zip")):
        print(f"[INFO] skip {key}: not a pipeline final zip")
        return {"status": "skipped", "key": key}
    name = key[len(TRIGGER_PREFIX):-4]
    print_key = f"{OUTPUT_PREFIX}{name}_print.html"
    layout_key = f"{OUTPUT_PREFIX}{name}_layout.html"
    review_key = f"{OUTPUT_PREFIX}{name}_review.json"

    # Idempotency: S3 may deliver an event twice. Skip if our output is
    # already newer than the pipeline result that triggered us.
    trig = head(bucket, key)
    done = head(bucket, print_key)
    if not FORCE and trig and done and done["LastModified"] >= trig["LastModified"]:
        print(f"[INFO] skip {name}: {print_key} is newer than {key}")
        return {"status": "skipped", "name": name, "reason": "already built"}

    pdf_key = None
    work = tempfile.mkdtemp(prefix="iris_layout_", dir="/tmp")
    t0 = time.time()
    try:
        with time_limit(seconds):
            # 1. the original article; its name is how IRIS knows the request
            pdf_key = find_original_pdf(bucket, name)

            # 2. pipeline output: BDA result.json + remediated.html
            out_zip_key = f"{PIPELINE_OUTPUT_PREFIX}{name}.zip"
            out_zip = os.path.join(work, "output.zip")
            s3.download_file(bucket, out_zip_key, out_zip)
            with zipfile.ZipFile(out_zip) as z:
                names = z.namelist()
            bda_member = pick_member(names, "result.json", "standard_output")
            if not bda_member:
                raise FileNotFoundError(f"no BDA result.json in s3://{bucket}/{out_zip_key}")
            bda_json = extract(out_zip, bda_member, work, "result.json")

            rem_html = None
            rem_member = pick_member(names, "remediated.html", "remediated_html/")
            if rem_member:
                rem_html = extract(out_zip, rem_member, work, "remediated.html")
            else:
                fin_zip = os.path.join(work, "final.zip")
                s3.download_file(bucket, key, fin_zip)
                with zipfile.ZipFile(fin_zip) as z:
                    rem_member = pick_member(z.namelist(), "remediated.html", "remediated_html/")
                if rem_member:
                    rem_html = extract(fin_zip, rem_member, work, "remediated.html")
            if not rem_html:
                print("[WARNING] no remediated.html found; alt text will come from BDA captions only")
            os.remove(out_zip)

            pdf_path = os.path.join(work, "original.pdf")
            s3.download_file(bucket, pdf_key, pdf_path)
            print(f"[INFO] {name}: pdf={pdf_key} bda={bda_member} remediated={rem_member}")

            # 3. build
            layout_html = os.path.join(work, "layout.html")
            print_html = os.path.join(work, "print.html")
            review_json = os.path.join(work, "review.json")
            report = iris_layout.build(pdf_path, bda_json, layout_html, rem_html, review_json,
                                       print_out=print_html, fonts_dir=FONTS_DIR)

            # 4. publish (print file last: its presence marks the job complete)
            upload(layout_html, bucket, layout_key, "text/html; charset=utf-8")
            upload(review_json, bucket, review_key, "application/json")
            upload(print_html, bucket, print_key, "text/html; charset=utf-8")
    except Exception as e:
        print(f"[ERROR] {name}: {e}\n{traceback.format_exc()}")
        # Without the upload, fall back to the name the pipeline's own
        # sanitizing left; right whenever the upload had a plain name.
        file = pdf_key[len(UPLOADS_PREFIX):] if pdf_key else f"{name}.pdf"
        notify_iris(status="error", message="remediation error", file=file,
                    payload={"step": "iris-layout", "bucket": bucket, "trigger_key": key,
                             "source_key": pdf_key, "error": str(e)[:500]})
        raise  # surfaces in Lambda error metrics; the deploy script sets retries to 0
    finally:
        shutil.rmtree(work, ignore_errors=True)

    figures_to_review = sum(1 for f in report.get("figures", []) if f.get("needs_review"))
    unplaced = sum(len(t.get("cells_unplaced", [])) for t in report.get("tables", []))
    summary = {
        "status": "done", "name": name, "seconds": round(time.time() - t0, 1),
        "print_html": f"s3://{bucket}/{print_key}",
        "layout_html": f"s3://{bucket}/{layout_key}",
        "review_json": f"s3://{bucket}/{review_key}",
        "figures_needing_alt_text_review": figures_to_review,
        "table_cells_unplaced": unplaced,
    }
    print(f"[INFO] {json.dumps(summary)}")
    notify_iris(
        status="success", message="layout complete", file=pdf_key[len(UPLOADS_PREFIX):],
        payload={"bucket": bucket, "key": print_key, "layout_key": layout_key, "review_key": review_key,
                 "source_key": pdf_key, "figures_needing_alt_text_review": figures_to_review,
                 "table_cells_unplaced": unplaced})
    return summary


def handler(event, context):
    if "awslogs" in event:
        data = json.loads(gzip.decompress(base64.b64decode(event["awslogs"]["data"])))
        return pipeline_failures.handle(data)

    results = []
    for rec in event.get("Records", []):
        info = rec.get("s3", {})
        bucket = info.get("bucket", {}).get("name")
        key = urllib.parse.unquote_plus(info.get("object", {}).get("key", ""))
        if bucket and key:
            seconds = None
            if context is not None:
                seconds = max(1, context.get_remaining_time_in_millis() // 1000 - TIME_MARGIN_SECONDS)
            results.append(process(bucket, key, seconds))
    return {"results": results}
