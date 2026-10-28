from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from slopekg.jobs import PipelineJobManager, PdfDirectoryWatcher
from slopekg.pipeline import DemoPipeline
from slopekg.storage import read_jsonl, write_jsonl
from tests.support import workspace_paths


class JobManagerTests(unittest.TestCase):
    def test_queued_job_blocks_second_start_and_snapshots_are_independent(self):
        manager = PipelineJobManager(Mock())
        with patch("slopekg.jobs.threading.Thread") as thread:
            first, created = manager.start({"ocr_pages": 0})
            self.assertTrue(created)
            second, created = manager.start({"ocr_pages": 1})
            self.assertFalse(created)
            self.assertEqual(first["id"], second["id"])
            first["options"]["ocr_pages"] = 99
            self.assertEqual(manager.get(first["id"])["options"]["ocr_pages"], 0)
            thread.assert_called_once()

    def test_completed_job_and_failed_job_keep_public_states(self):
        pipeline = Mock()
        pipeline.run.return_value = {"done": True}
        manager = PipelineJobManager(pipeline)
        with patch("slopekg.jobs.threading.Thread"):
            first, _ = manager.start({})
            manager._run(first["id"])
            self.assertEqual(manager.latest()["status"], "completed")
            self.assertEqual(manager.latest()["progress"], 100)
            pipeline.run.side_effect = ValueError("example failure")
            second, _ = manager.start({})
            manager._run(second["id"])
            self.assertEqual(manager.latest()["status"], "failed")
            self.assertEqual(manager.latest()["error"], "example failure")

    def test_watcher_uses_supplied_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = workspace_paths(Path(directory))
            paths.raw_dir.mkdir(parents=True)
            (paths.raw_dir / "sample.pdf").write_bytes(b"%PDF")
            watcher = PdfDirectoryWatcher(Mock(), paths=paths)
            self.assertEqual([row[0] for row in watcher.snapshot()], ["sample.pdf"])

    def test_watcher_schedules_deleted_pdf_and_parser_removes_its_cached_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = workspace_paths(Path(directory))
            paths.raw_dir.mkdir(parents=True)
            files = [paths.raw_dir / name for name in ("keep.pdf", "remove.pdf")]
            for file in files:
                file.write_bytes(b"%PDF-cached")
            write_jsonl(paths.parsed_dir / "documents.jsonl", [
                {"id": file.stem, "file_name": file.name,
                 "source_size_bytes": file.stat().st_size,
                 "source_mtime_ns": file.stat().st_mtime_ns}
                for file in files
            ])
            write_jsonl(paths.parsed_dir / "pages.jsonl", [
                {"id": "page-" + file.stem, "document_id": file.stem} for file in files
            ])
            manager = Mock()
            manager.start.return_value = ({"id": "auto"}, True)
            watcher = PdfDirectoryWatcher(manager, paths=paths)
            self.assertFalse(watcher.check_once())
            self.assertFalse(watcher.check_once())
            manager.start.assert_not_called()

            files[1].unlink()
            self.assertFalse(watcher.check_once())  # Wait for a stable directory.
            self.assertTrue(watcher.check_once())
            self.assertFalse(watcher.check_once())  # Do not queue the same change twice.
            manager.start.assert_called_once()
            self.assertEqual(manager.start.call_args.kwargs["trigger"], "directory_watch")
            self.assertFalse(manager.start.call_args.args[0]["reuse_parsed"])

            DemoPipeline(paths).parse_documents()
            self.assertEqual([row["file_name"] for row in read_jsonl(
                paths.parsed_dir / "documents.jsonl")], ["keep.pdf"])
            self.assertEqual([row["document_id"] for row in read_jsonl(
                paths.parsed_dir / "pages.jsonl")], ["keep"])
