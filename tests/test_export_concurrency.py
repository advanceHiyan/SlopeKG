from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest

from slopekg.server import reserve_export_destination


class ExportConcurrencyTests(unittest.TestCase):
    def test_simultaneous_reports_never_overwrite_existing_or_each_other(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            original = folder / 'report.html'
            original.write_text('original', encoding='utf-8')
            start = threading.Barrier(8)
            def save(index):
                start.wait()
                with reserve_export_destination(folder, 'report.html') as destination:
                    destination.write_text(str(index), encoding='utf-8')
                    return destination
            with ThreadPoolExecutor(max_workers=8) as workers:
                reports = list(workers.map(save, range(8)))
            self.assertEqual(len(set(reports)), 8)
            self.assertEqual({p.read_text(encoding='utf-8') for p in reports}, {str(i) for i in range(8)})
            self.assertEqual(original.read_text(encoding='utf-8'), 'original')

    def test_failed_report_cleans_its_reservation_without_removing_previous_report(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            original = folder / 'report.html'
            original.write_text('previous', encoding='utf-8')
            with self.assertRaises(ValueError):
                with reserve_export_destination(folder, 'report.html') as destination:
                    destination.write_text('partial', encoding='utf-8')
                    raise ValueError('export failed')
            self.assertEqual(list(folder.iterdir()), [original])
