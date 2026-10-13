"""Regression cases found during the October code review; no private inputs."""
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from io import BytesIO
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from slopekg import server
from slopekg.manual import save_slope_override
from slopekg.risk import evaluate_condition, maximum_number
from slopekg.jobs import PdfDirectoryWatcher
from tests.support import workspace_paths


class ReviewRegressionTests(unittest.TestCase):
    def handler(self, path, raw=b"{}"):
        handler = object.__new__(server.SlopeKGHandler)
        handler.path = path
        handler.headers = {"Content-Length": str(len(raw))}
        handler.rfile = BytesIO(raw)
        handler.write_json = Mock()
        handler.request_origin_allowed = Mock(return_value=True)
        handler.pipeline = Mock()
        return handler

    def test_json_body_must_be_an_object(self):
        for raw in (b"[]", b"null", b"true", b"1", b'"text"'):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    self.handler("/api/pipeline/jobs", raw).read_json_body()

    def test_invalid_json_requests_return_400_without_starting_work(self):
        for path in ("/api/pipeline/jobs", "/api/rules/jobs", "/api/risk/assess", "/api/pipeline/run"):
            for raw in (b"[]", b"null", b"{", b'{"value":"\xff"}', b'{"value":NaN}', b'{"value":1e400}'):
                with self.subTest(path=path, raw=raw), patch("slopekg.server.JOB_MANAGER") as jobs, patch("slopekg.server.RULE_JOB_MANAGER") as rules:
                    handler = self.handler(path, raw)
                    handler.do_POST()
                    self.assertEqual(handler.write_json.call_args.kwargs["status"], HTTPStatus.BAD_REQUEST)
                    jobs.start.assert_not_called()
                    rules.start.assert_not_called()
                    handler.pipeline.run.assert_not_called()

    def test_missing_or_nonfinite_numbers_do_not_satisfy_inequality(self):
        for value in (None, "", [], {}, "NaN", "1e400", 10**400, float("inf")):
            with self.subTest(value=value):
                self.assertFalse(evaluate_condition(value, "neq", 10))
        self.assertTrue(evaluate_condition(0, "neq", 10))
        self.assertTrue(evaluate_condition("岩质", "neq", "土质"))

    def test_invalid_numbers_do_not_contaminate_maximum(self):
        self.assertEqual(maximum_number("NaN", "inf", 12), 12)
        self.assertIsNone(maximum_number("NaN", "-inf"))
        self.assertEqual(maximum_number(10**400, 12), 12)

    def test_nonfinite_manual_numbers_never_get_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = workspace_paths(Path(directory))
            for value in ("NaN", "inf", float("nan"), float("-inf"), 10**400):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    save_slope_override("sample", {"fields": {"slope_height_m": value}}, paths)
            self.assertFalse(paths.slope_overrides_json.exists())

    def test_concurrent_uploads_keep_each_original_file(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = workspace_paths(Path(directory))
            original = server.unique_destination

            def delayed_destination(*args):
                result = original(*args)
                time.sleep(0.01)  # Make the former check/write race reproducible.
                return result

            def upload(index):
                payload = f"%PDF-1.4\nsource-{index}".encode()
                raw = (b'--sample\r\nContent-Disposition: form-data; name="file"; filename="same.pdf"\r\n'
                       b'Content-Type: application/pdf\r\n\r\n' + payload + b'\r\n--sample--\r\n')
                handler = self.handler("/api/documents/upload", raw)
                handler.headers["Content-Type"] = "multipart/form-data; boundary=sample"
                handler.handle_pdf_upload()
                return payload

            with patch("slopekg.server.PATHS", paths), patch("slopekg.server.JOB_MANAGER") as manager, patch("slopekg.server.unique_destination", side_effect=delayed_destination):
                manager.start.return_value = ({}, False)
                with ThreadPoolExecutor(max_workers=8) as executor:
                    expected = set(executor.map(upload, range(8)))
            actual = {file.read_bytes() for file in paths.raw_dir.glob("*.pdf")}
            self.assertEqual(actual, expected)

    def test_listener_can_be_started_again_after_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            watcher = PdfDirectoryWatcher(Mock(), interval_seconds=0.01, paths=workspace_paths(Path(directory)))
            try:
                watcher.start()
                watcher.stop()
                self.assertFalse(watcher._thread.is_alive())
                watcher.start()
                self.assertTrue(watcher._thread.is_alive())
                self.assertFalse(watcher._stop.is_set())
            finally:
                watcher.stop()

    def test_listener_start_failure_keeps_original_error_and_closes_server(self):
        watcher = PdfDirectoryWatcher(Mock())
        with patch("slopekg.server.PDF_WATCHER", watcher), patch("slopekg.server.ThreadingHTTPServer") as factory, patch("slopekg.jobs.threading.Thread.start", side_effect=RuntimeError("thread start failed")):
            with self.assertRaisesRegex(RuntimeError, "thread start failed"):
                server.run_server()
            factory.return_value.server_close.assert_called_once()

    def test_upload_is_only_visible_after_the_full_file_is_written(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            replace = Path.replace
            observations = []

            def inspect_replace(temporary, destination):
                observations.append((list(root.glob("*.pdf")), temporary.read_bytes()))
                return replace(temporary, destination)

            with patch.object(Path, "replace", inspect_replace):
                destination = server.save_pdf_upload(root, "sample.pdf", b"%PDF-complete")
            self.assertEqual(observations, [([], b"%PDF-complete")])
            self.assertEqual(destination.read_bytes(), b"%PDF-complete")
            self.assertEqual(list(root.glob("*.tmp")), [])

    def test_failed_upload_publication_leaves_no_partial_or_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(Path, "replace", side_effect=OSError("disk unavailable")):
                with self.assertRaises(OSError):
                    server.save_pdf_upload(root, "sample.pdf", b"%PDF-complete")
            self.assertEqual(list(root.iterdir()), [])

    def test_listener_does_not_start_when_server_binding_fails(self):
        with patch("slopekg.server.ThreadingHTTPServer", side_effect=OSError("port busy")), patch("slopekg.server.PDF_WATCHER") as watcher:
            with self.assertRaises(OSError):
                server.run_server()
            watcher.start.assert_not_called()

    def test_server_shutdown_stops_listener_and_closes_socket(self):
        with patch("slopekg.server.ThreadingHTTPServer") as factory, patch("slopekg.server.PDF_WATCHER") as watcher:
            factory.return_value.serve_forever.side_effect = KeyboardInterrupt
            with self.assertRaises(KeyboardInterrupt):
                server.run_server()
            watcher.stop.assert_called_once()
            factory.return_value.server_close.assert_called_once()
