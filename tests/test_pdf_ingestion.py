from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from slopekg.server import PdfDirectoryWatcher, document_inventory, parse_pdf_uploads, safe_export_name, safe_pdf_name, unique_destination


class PdfUploadValidationTests(unittest.TestCase):
    def test_pdf_name_keeps_chinese_and_removes_path(self):
        self.assertEqual(safe_pdf_name(r"C:\fake\边坡资料.pdf"), "边坡资料.pdf")

    def test_non_pdf_name_is_rejected(self):
        with self.assertRaises(ValueError):
            safe_pdf_name("边坡资料.docx")

    def test_export_name_is_sanitized_and_forced_to_html(self):
        self.assertEqual(safe_export_name(r"..\交通部:成果*展示"), "交通部_成果_展示.html")
        self.assertEqual(safe_export_name("自定义名称.HTML"), "自定义名称.html")

    def test_export_name_cannot_escape_share_directory(self):
        self.assertEqual(safe_export_name(r"..\..\evil.html"), "evil.html")

    def test_existing_file_is_not_overwritten(self):
        with patch.object(Path, "exists", side_effect=[True, False]):
            self.assertEqual(unique_destination(Path("uploads"), "资料.pdf").name, "资料_1.pdf")

    def test_document_inventory_reports_raw_pdfs(self):
        payload = document_inventory()
        self.assertGreaterEqual(payload["count"], 1)
        self.assertEqual(payload["count"], len(payload["rows"]))
        self.assertTrue(all(row["file_name"].lower().endswith(".pdf") for row in payload["rows"]))

    def test_valid_multipart_pdf_is_extracted_without_writing(self):
        boundary = "slopekg-test-boundary"
        raw = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="files"; filename="边坡资料.pdf"\r\n'
            "Content-Type: application/pdf\r\n\r\n"
        ).encode("utf-8") + b"%PDF-1.4\n%%EOF\r\n" + f"--{boundary}--\r\n".encode("ascii")
        rows = parse_pdf_uploads(f"multipart/form-data; boundary={boundary}", raw)
        self.assertEqual(rows, [("边坡资料.pdf", b"%PDF-1.4\n%%EOF")])

    def test_directory_watcher_waits_for_stable_snapshot_then_schedules(self):
        manager = Mock()
        manager.start.return_value = ({"id": "auto"}, True)
        watcher = PdfDirectoryWatcher(manager, interval_seconds=0.01)
        snapshot = (("新增资料.pdf", 123, 456),)
        with patch.object(watcher, "snapshot", return_value=snapshot), patch(
            "slopekg.server.document_inventory", return_value={"unparsed_count": 1}
        ):
            self.assertFalse(watcher.check_once())
            self.assertTrue(watcher.check_once())
        manager.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
