"""
Offline tests for the handler, the pipeline-failure parser and the IRIS
notifier. Standard library only: boto3, botocore and iris_layout are stubbed,
so nothing touches AWS or IRIS.

    python3 -m unittest discover -s test -v      (from the package folder)
"""
import base64
import gzip
import io
import json
import os
import re
import sys
import time
import types
import unittest
import urllib.error
import zipfile
from datetime import datetime, timedelta, timezone
from unittest import mock

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
sys.path.insert(0, SRC)


class ClientError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


# Stubs for the modules the Lambda runtime / package provide.
_boto3 = types.ModuleType("boto3")
_boto3.client = lambda *a, **k: mock.MagicMock()
_botocore = types.ModuleType("botocore")
_botocore_exc = types.ModuleType("botocore.exceptions")
_botocore_exc.ClientError = ClientError
_layout = types.ModuleType("iris_layout")
_layout.build = None
sys.modules.update({"boto3": _boto3, "botocore": _botocore, "botocore.exceptions": _botocore_exc,
                    "iris_layout": _layout})

import iris_notify          # noqa: E402
import lambda_function      # noqa: E402
import pipeline_failures    # noqa: E402

BUCKET = "pdf2html-bucket"
T0 = datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)


def awslogs_event(lines, group="/aws/lambda/Pdf2HtmlPipeline", stream="2026/11/24/[$LATEST]abc", ts=1_000_000):
    data = {"messageType": "DATA_MESSAGE", "logGroup": group, "logStream": stream,
            "logEvents": [{"id": str(i), "timestamp": ts, "message": l} for i, l in enumerate(lines)]}
    return {"awslogs": {"data": base64.b64encode(gzip.compress(json.dumps(data).encode())).decode()}}


# --------------------------------------------------------------------------
# pipeline failure lines
# --------------------------------------------------------------------------

class MatchFailureTest(unittest.TestCase):
    def test_lines_with_key(self):
        cases = {
            "[ERROR] Failed to download s3://b/uploads/My Article.pdf: An error occurred (403): Forbidden\n":
                ("uploads/My Article.pdf", "An error occurred (403): Forbidden"),
            "[ERROR] Processing uploads/1303997.pdf failed: BDA job FAILED\n":
                ("uploads/1303997.pdf", "BDA job FAILED"),
            "[ERROR] Processing uploads/Report.PDF failed: x":
                ("uploads/Report.PDF", "x"),
        }
        for line, want in cases.items():
            self.assertEqual(pipeline_failures.match_failure(line), want, line)

    def test_lines_without_key(self):
        for line in ["[ERROR] Creating or uploading zip failed: disk full",
                     "[ERROR] Unhandled exception: boom",
                     "2026-11-24T10:00:00.000Z 0f1e2d3c Task timed out after 900.00 seconds",
                     "RequestId: 0f1e2d3c Error: Runtime exited with error: signal: killed"]:
            hit = pipeline_failures.match_failure(line)
            self.assertIsNotNone(hit, line)
            self.assertIsNone(hit[0], line)
            self.assertTrue(hit[1])

    def test_not_failures(self):
        for line in ["[INFO] Processing s3://b/uploads/a.pdf",
                     "START RequestId: 0f1e2d3c Version: $LATEST",
                     "[WARNING] Failed to delete BDA input file: nope",
                     # Python logging (tab after the level): a library's own, possibly recovered, error
                     "[ERROR]\t2026-11-24T10:00:00.000Z\t0f1e2d3c\tProcessing page 3 failed: retrying",
                     "Traceback (most recent call last):"]:
            self.assertIsNone(pipeline_failures.match_failure(line), line)

    def test_subscription_filter_covers_every_pattern(self):
        # each failure line must contain one of the filter's quoted terms
        terms = re.findall(r'\?"([^"]+)"', pipeline_failures.SUBSCRIPTION_FILTER)
        self.assertEqual(len(terms), len(pipeline_failures.FAILURE_LINES))
        for line in ["[ERROR] Failed to download s3://b/uploads/a.pdf: e",
                     "[ERROR] Processing uploads/a.pdf failed: e",
                     "[ERROR] Creating or uploading zip failed: e",
                     "[ERROR] Unhandled exception: e",
                     "x Task timed out after 900.00 seconds",
                     "Error: Runtime exited with error: signal: killed"]:
            self.assertTrue(any(t in line for t in terms), line)


class FakeLogs:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def filter_log_events(self, **kw):
        self.calls.append(kw)
        i = int(kw.get("nextToken", 0))
        page = {"events": [{"message": m} for m in self.pages[i]]}
        if i + 1 < len(self.pages):
            page["nextToken"] = str(i + 1)
        return page


class HandlePipelineFailureTest(unittest.TestCase):
    def setUp(self):
        self.notify = mock.patch.object(pipeline_failures, "notify_iris", return_value=True).start()
        mock.patch.object(pipeline_failures.time, "sleep").start()
        self.addCleanup(mock.patch.stopall)

    def run_handler(self, lines, **kw):
        return lambda_function.handler(awslogs_event(lines, **kw), None)

    def test_key_in_line(self):
        out = self.run_handler(["[ERROR] Processing uploads/My Article.pdf failed: BDA job failed\n",
                                "[ERROR] Processing uploads/My Article.pdf failed: BDA job failed\n"])
        self.assertEqual(out, {"failures": 1, "sent": 1})
        kw = self.notify.call_args.kwargs
        self.assertEqual((kw["status"], kw["message"], kw["file"]), ("error", "remediation error", "My Article.pdf"))
        self.assertEqual(kw["payload"]["source_key"], "uploads/My Article.pdf")
        self.assertEqual(kw["payload"]["error"], "BDA job failed")
        self.assertEqual(kw["payload"]["step"], "pdf2html")

    def test_key_from_stream_uses_current_invocation(self):
        logs = FakeLogs([
            ["START RequestId: a Version: $LATEST", "[INFO] Processing s3://b/uploads/old.pdf",
             "START RequestId: b Version: $LATEST"],
            ["[INFO] Processing s3://b/uploads/new one.pdf"],
        ])
        with mock.patch.object(pipeline_failures, "logs_client", return_value=logs):
            out = self.run_handler(["2026-11-24T10:00:00.000Z b Task timed out after 900.00 seconds"],
                                   ts=5_000_000, stream="s1")
        self.assertEqual(out["sent"], 1)
        self.assertEqual(self.notify.call_args.kwargs["file"], "new one.pdf")
        call = logs.calls[0]
        self.assertEqual(call["logStreamNames"], ["s1"])
        self.assertEqual(call["endTime"], 5_000_000)
        self.assertEqual(call["startTime"], 5_000_000 - 16 * 60 * 1000)
        self.assertEqual(call["filterPattern"], pipeline_failures.LOOKBACK_FILTER)

    def test_no_key_for_this_invocation(self):
        # failed before printing its Processing line: don't blame the previous article
        logs = FakeLogs([["START RequestId: a", "[INFO] Processing s3://b/uploads/old.pdf",
                          "START RequestId: b"]])
        with mock.patch.object(pipeline_failures, "logs_client", return_value=logs):
            out = self.run_handler(["[ERROR] Unhandled exception: boom"])
        self.assertEqual(out, {"failures": 0, "sent": 0})
        self.notify.assert_not_called()
        self.assertEqual(len(logs.calls), 3)  # retried for late-arriving lines

    def test_lookback_error_is_not_fatal(self):
        logs = mock.MagicMock()
        logs.filter_log_events.side_effect = RuntimeError("AccessDenied")
        with mock.patch.object(pipeline_failures, "logs_client", return_value=logs):
            out = self.run_handler(["[ERROR] Unhandled exception: boom",
                                    "[ERROR] Processing uploads/x.pdf failed: e"])
        self.assertEqual(out, {"failures": 1, "sent": 1})
        self.assertEqual(self.notify.call_args.kwargs["file"], "x.pdf")

    def test_control_message(self):
        data = {"messageType": "CONTROL_MESSAGE", "logEvents": []}
        event = {"awslogs": {"data": base64.b64encode(gzip.compress(json.dumps(data).encode())).decode()}}
        self.assertEqual(lambda_function.handler(event, None), {"sent": 0})
        self.notify.assert_not_called()

    def test_non_upload_key_ignored(self):
        out = self.run_handler(["[ERROR] Processing other/x.pdf failed: e"])
        self.assertEqual(out["sent"], 0)
        self.notify.assert_not_called()


# --------------------------------------------------------------------------
# layout (S3 event)
# --------------------------------------------------------------------------

class FakeS3:
    """Just enough of the S3 client for process()."""

    def __init__(self, objects):
        self.objects = dict(objects)      # key -> (bytes, LastModified)
        self.uploaded = []

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError("404")
        return {"LastModified": self.objects[Key][1]}

    def download_file(self, bucket, key, path):
        if key not in self.objects:
            raise ClientError("404")
        with open(path, "wb") as f:
            f.write(self.objects[key][0])

    def upload_file(self, path, bucket, key, ExtraArgs=None):
        with open(path, "rb") as f:
            self.objects[key] = (f.read(), T0 + timedelta(hours=1))
        self.uploaded.append((key, ExtraArgs["ContentType"]))

    def get_paginator(self, name):
        s3 = self

        class P:
            def paginate(self, Bucket, Prefix, Delimiter):
                keys = [k for k in s3.objects if k.startswith(Prefix) and "/" not in k[len(Prefix):]]
                yield {"Contents": [{"Key": k} for k in keys]}
        return P()


def output_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("My_Article/standard_output/0/result.json", '{"elements": []}')
        z.writestr("My_Article/remediated_html/remediated.html", "<img src='a.png' alt='x'>")
    return buf.getvalue()


def fake_build(pdf, bda, out, rem, review, print_out=None, fonts_dir=None):
    for p in (out, review, print_out):
        with open(p, "w") as f:
            f.write("x")
    with open(bda) as f:
        assert f.read() == '{"elements": []}'
    with open(rem) as f:
        assert f.read().startswith("<img")
    return {"figures": [{"needs_review": True}, {"needs_review": False}],
            "tables": [{"cells_unplaced": ["a", "b"]}]}


class ProcessTest(unittest.TestCase):
    KEY = "remediated/final_My_Article.zip"

    def setUp(self):
        self.s3 = FakeS3({
            "uploads/My Article.pdf": (b"%PDF", T0),
            "output/My_Article.zip": (output_zip(), T0),
            self.KEY: (b"zip", T0),
        })
        mock.patch.object(lambda_function, "s3", self.s3).start()
        self.notify = mock.patch.object(lambda_function, "notify_iris", return_value=True).start()
        self.build = mock.patch.object(lambda_function.iris_layout, "build", side_effect=fake_build,
                                       create=True).start()
        self.addCleanup(mock.patch.stopall)

    def s3_event(self, key=KEY):
        return {"Records": [{"s3": {"bucket": {"name": BUCKET}, "object": {"key": key}}}]}

    def test_success(self):
        out = lambda_function.handler(self.s3_event(), None)["results"][0]
        self.assertEqual(out["status"], "done")
        self.assertEqual(out["figures_needing_alt_text_review"], 1)
        self.assertEqual(out["table_cells_unplaced"], 2)
        self.assertEqual([k for k, _ in self.s3.uploaded],
                         ["iris/My_Article_layout.html", "iris/My_Article_review.json", "iris/My_Article_print.html"])
        kw = self.notify.call_args.kwargs
        self.assertEqual((kw["status"], kw["message"], kw["file"]), ("success", "layout complete", "My Article.pdf"))
        self.assertEqual(kw["payload"]["key"], "iris/My_Article_print.html")
        self.assertEqual(kw["payload"]["bucket"], BUCKET)
        self.assertEqual(kw["payload"]["source_key"], "uploads/My Article.pdf")
        self.assertEqual(self.notify.call_count, 1)

    def test_url_encoded_trigger_key(self):
        self.s3.objects["remediated/final_A+B.zip"] = (b"zip", T0)
        # A+B has no matching upload; the point is the key was decoded before use
        with self.assertRaises(FileNotFoundError):
            lambda_function.handler(self.s3_event("remediated/final_A%2BB.zip"), None)
        self.assertEqual(self.notify.call_args.kwargs["payload"]["trigger_key"], "remediated/final_A+B.zip")

    def test_layout_failure(self):
        self.build.side_effect = ValueError("bad BDA")
        with self.assertRaises(ValueError):
            lambda_function.handler(self.s3_event(), None)
        kw = self.notify.call_args.kwargs
        self.assertEqual((kw["status"], kw["message"], kw["file"]), ("error", "remediation error", "My Article.pdf"))
        self.assertEqual(kw["payload"]["step"], "iris-layout")
        self.assertEqual(kw["payload"]["error"], "bad BDA")
        self.assertEqual(self.notify.call_count, 1)
        self.assertEqual(self.s3.uploaded, [])

    def test_missing_upload_falls_back_to_sanitized_name(self):
        del self.s3.objects["uploads/My Article.pdf"]
        with self.assertRaises(FileNotFoundError):
            lambda_function.handler(self.s3_event(), None)
        kw = self.notify.call_args.kwargs
        self.assertEqual((kw["message"], kw["file"]), ("remediation error", "My_Article.pdf"))
        self.assertIsNone(kw["payload"]["source_key"])

    def test_timeout_reports_failure(self):
        self.build.side_effect = lambda *a, **k: time.sleep(5)
        ctx = mock.Mock(get_remaining_time_in_millis=lambda: (lambda_function.TIME_MARGIN_SECONDS + 1) * 1000)
        t = time.time()
        with self.assertRaises(lambda_function.LayoutTimeout):
            lambda_function.handler(self.s3_event(), ctx)
        self.assertLess(time.time() - t, 3)
        self.assertEqual(self.notify.call_args.kwargs["message"], "remediation error")

    def test_alarm_cleared_after_success(self):
        ctx = mock.Mock(get_remaining_time_in_millis=lambda: 300_000)
        lambda_function.handler(self.s3_event(), ctx)
        import signal
        self.assertEqual(signal.alarm(0), 0)  # nothing left pending

    def test_already_built_is_skipped(self):
        self.s3.objects["iris/My_Article_print.html"] = (b"x", T0 + timedelta(minutes=1))
        out = lambda_function.handler(self.s3_event(), None)["results"][0]
        self.assertEqual(out["status"], "skipped")
        self.notify.assert_not_called()

    def test_not_a_final_zip(self):
        out = lambda_function.handler(self.s3_event("remediated/other.zip"), None)["results"][0]
        self.assertEqual(out["status"], "skipped")
        self.notify.assert_not_called()


# --------------------------------------------------------------------------
# notifier
# --------------------------------------------------------------------------

class NotifyTest(unittest.TestCase):
    def setUp(self):
        iris_notify._config.update(value={"url": "https://iris/v1/notify", "api_key": "k"}, fetched_at=time.time())
        mock.patch.object(iris_notify, "NOTIFY_ENABLED", True).start()
        mock.patch.object(iris_notify.time, "sleep").start()
        self.addCleanup(mock.patch.stopall)

    def test_body_matches_iris_schema(self):
        with mock.patch.object(iris_notify, "_post", return_value=200) as post:
            self.assertTrue(iris_notify.notify_iris("success", "layout complete", "a b.pdf", {"key": "iris/x"}))
        url, key, data = post.call_args.args
        self.assertEqual((url, key), ("https://iris/v1/notify", "k"))
        self.assertEqual(json.loads(data), {"sender": "aws-iris-layout", "status": "success", "file": "a b.pdf",
                                            "message": "layout complete", "payload": {"key": "iris/x"}})

    def test_4xx_not_retried(self):
        err = urllib.error.HTTPError("u", 400, "Bad Request", {}, io.BytesIO(b'{"message":"Unknown message"}'))
        with mock.patch.object(iris_notify, "_post", side_effect=err) as post:
            self.assertFalse(iris_notify.notify_iris("success", "layout complete", "a.pdf"))
        self.assertEqual(post.call_count, 1)

    def test_5xx_and_network_retried(self):
        err = urllib.error.HTTPError("u", 503, "Unavailable", {}, io.BytesIO(b""))
        with mock.patch.object(iris_notify, "_post", side_effect=[err, OSError("reset"), 200]) as post:
            self.assertTrue(iris_notify.notify_iris("error", "remediation error", "a.pdf"))
        self.assertEqual(post.call_count, 3)
        with mock.patch.object(iris_notify, "_post", side_effect=OSError("down")) as post:
            self.assertFalse(iris_notify.notify_iris("error", "remediation error", "a.pdf"))
        self.assertEqual(post.call_count, 3)

    def test_disabled(self):
        with mock.patch.object(iris_notify, "NOTIFY_ENABLED", False), \
                mock.patch.object(iris_notify, "_post") as post:
            self.assertFalse(iris_notify.notify_iris("error", "remediation error", "a.pdf"))
        post.assert_not_called()

    def test_bad_secret(self):
        iris_notify._config.update(value=None)
        sm = mock.MagicMock()
        sm.get_secret_value.return_value = {"SecretString": '{"url": "u"}'}
        with mock.patch.object(iris_notify.boto3, "client", return_value=sm), \
                mock.patch.object(iris_notify, "_post") as post:
            self.assertFalse(iris_notify.notify_iris("error", "remediation error", "a.pdf"))
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
