from pathlib import Path
from hashlib import sha256
import tempfile
import unittest

from slopekg.queries import document_inventory
from slopekg.storage import write_jsonl
from tests.support import workspace_paths


class InventoryFreshnessTests(unittest.TestCase):
    def test_unchanged_size_and_time_do_not_label_a_changed_pdf_as_current(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = workspace_paths(Path(directory))
            paths.raw_dir.mkdir(parents=True)
            file = paths.raw_dir / 'sample.pdf'
            file.write_bytes(b'new content')
            write_jsonl(paths.parsed_dir / 'documents.jsonl', [{
                'id': 'doc', 'file_name': file.name, 'source_size_bytes': file.stat().st_size,
                'source_mtime_ns': file.stat().st_mtime_ns, 'source_sha256': sha256(b'old content').hexdigest(),
            }])
            result = document_inventory(paths)
            self.assertFalse(result['rows'][0]['parsed'])
            self.assertEqual(result['rows'][0]['change_status'], 'changed')
            self.assertEqual(result['stale_count'], 1)
