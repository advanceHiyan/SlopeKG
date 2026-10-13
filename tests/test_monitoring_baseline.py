from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlparse

from slopekg.monitoring_baseline import evaluate_component, build_baseline_experiment, baseline_payload, experiment_dir, baseline_interfaces, export_baseline_report
from slopekg.monitoring import build_huichang_pilot, PILOT_FILES
from slopekg.server import SlopeKGHandler
from tests.support import workspace_paths
from tests.test_monitoring import write_sample


def sample_rows(n=60, value=lambda i: i):
    start = date(2026, 1, 1)
    return [{"id": f"o{i}", "day": (start + timedelta(days=i)).isoformat(),
             "time": (start + timedelta(days=i)).isoformat() + " 00:00:00",
             "components": [value(i)], "quality_flags": []} for i in range(n)]


class TemporalBaselineTests(unittest.TestCase):
    def test_linear_sequence_drift_beats_persistence_on_identical_targets(self):
        result = evaluate_component(sample_rows(value=lambda i: 2 * i), 0)
        self.assertEqual(result["metrics"]["test"]["methods"]["recent_drift"]["mae"], 0)
        self.assertEqual(result["metrics"]["test"]["methods"]["persistence"]["mae"], 2)
        self.assertEqual(result["metrics"]["test"]["methods"]["persistence"]["count"], 12)
        self.assertEqual(result["training_scale"], 2)

    def test_future_values_do_not_change_past_predictions_or_training_scale(self):
        original = sample_rows()
        changed = deepcopy(original)
        for row in changed[55:]:
            row["components"] = [90000]
        first, second = evaluate_component(original, 0), evaluate_component(changed, 0)
        self.assertEqual(first["training_scale"], second["training_scale"])
        self.assertEqual([r for r in first["predictions"] if r["target_day"] < changed[55]["day"]],
                         [r for r in second["predictions"] if r["target_day"] < changed[55]["day"]])
        for r in first["predictions"]:
            target_index = int(r["target_id"][1:])
            self.assertTrue(all(int(i[1:]) < target_index for i in r["input_ids"]))

    def test_gap_is_not_compressed_or_interpolated_and_restarts_history(self):
        rows = sample_rows()
        del rows[50]
        result = evaluate_component(rows, 0)
        target_ids = {r["target_id"] for r in result["predictions"]}
        self.assertFalse(target_ids & {f"o{i}" for i in range(50, 58)})
        self.assertIn("o58", target_ids)
        self.assertEqual(result["metrics"]["test"]["coverage"]["calendar_days"], 12)

    def test_constant_and_zero_values_are_preserved_without_fake_skill_score(self):
        result = evaluate_component(sample_rows(value=lambda i: 0), 0)
        self.assertEqual(result["metrics"]["test"]["methods"]["persistence"]["mae"], 0)
        self.assertIsNone(result["metrics"]["test"]["methods"]["persistence"]["mase"])
        self.assertIsNone(result["metrics"]["test"]["drift_mae_improvement_fraction"])
        self.assertIn("all_zero_training_values_not_proof_of_sensor_health", result["flags"])
        self.assertFalse(result["rejected"])

    def test_duplicated_date_rejects_all_rows_instead_of_picking_a_winner(self):
        rows = sample_rows()
        rows.append({**rows[50], "id": "duplicate", "components": [999]})
        result = evaluate_component(rows, 0)
        self.assertEqual(sum(r["reason"] == "ambiguous_source_date" for r in result["rejected"]), 2)
        self.assertFalse(any(r["target_id"] == "o50" for r in result["predictions"]))

    def test_invalid_values_times_and_missing_component_are_explicit(self):
        rows = sample_rows()
        rows[49]["components"] = [float('nan')]
        rows[50]["components"] = []
        rows[51]["time"] = 'bad'
        result = evaluate_component(rows, 0)
        self.assertEqual(len(result["rejected"]), 3)
        self.assertFalse(any(r["target_id"] in {'o49', 'o50', 'o51'} for r in result["predictions"]))

    def test_short_series_and_invalid_parameters_do_not_claim_usable_model(self):
        self.assertEqual(evaluate_component(sample_rows(5), 0)["status"], "insufficient_data")
        for kwargs in ({"component": -1}, {"component": True}, {"component": 0, "lookback": 3}):
            with self.assertRaises(ValueError):
                evaluate_component(sample_rows(), **kwargs)


class PublishedBaselineTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.paths = workspace_paths(Path(temp.name))
        self.source = self.paths.root / 'data/202610/监测数据/会昌'
        self.source.mkdir(parents=True)
        for name in PILOT_FILES:
            header = ['序号', '设备名称', '时间', '数值']
            rows = [[i, 'sensor', r['day'], r['components'][0]] for i, r in enumerate(sample_rows())]
            write_sample(self.source / name, header, rows)
        build_huichang_pilot(self.paths)

    def test_published_report_retains_source_refs_and_no_risk_or_deployment_claim(self):
        report = build_baseline_experiment(self.paths)
        self.assertEqual(len(report['series']), 2)
        self.assertFalse(report['risk_eligible'])
        self.assertFalse(report['deployment_ready'])
        self.assertFalse(report['actual_availability_verified'])
        self.assertEqual(baseline_payload(self.paths), report)
        for series in report['series']:
            for row in series['components'][0]['predictions']:
                refs = report['source_references']
                self.assertTrue(all(refs[i]['time'] < refs[row['target_id']]['time'] for i in row['input_ids']))
                self.assertTrue(refs[row['target_id']]['source']['cells'])

    def test_missing_report_is_waiting_and_modified_selected_source_is_rejected(self):
        self.assertEqual(baseline_payload(self.paths)['status'], 'not_generated')
        build_baseline_experiment(self.paths)
        (self.source / '05LF03(裂缝)累计位移_日.xlsx').write_bytes(b'modified')
        with self.assertRaises(ValueError):
            baseline_payload(self.paths)
        payload = baseline_interfaces({'interfaces': []}, self.paths)
        self.assertEqual(payload['interfaces'][0]['status'], 'data_unavailable')

    def test_modified_report_is_rejected_and_failed_run_keeps_active_pointer(self):
        build_baseline_experiment(self.paths)
        pointer = (experiment_dir(self.paths) / 'catalog.json').read_bytes()
        with patch('slopekg.monitoring_baseline.evaluate_component', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                build_baseline_experiment(self.paths)
        self.assertEqual((experiment_dir(self.paths) / 'catalog.json').read_bytes(), pointer)
        import json
        report_file = experiment_dir(self.paths) / json.loads(pointer)['report_file']
        report_file.write_text('{}', encoding='utf-8')
        with self.assertRaises(OSError):
            baseline_payload(self.paths)

    def test_unused_table_change_does_not_invalidate_compatible_selected_sources(self):
        report = build_baseline_experiment(self.paths)
        write_sample(self.source / PILOT_FILES[0], ['序号', '设备名称', '时间', '数值'], [[1, 'rain', '2026-01-01', 123]])
        build_huichang_pilot(self.paths)
        self.assertEqual(baseline_payload(self.paths)['run_id'], report['run_id'])

    def test_http_route_waits_without_data_and_rejects_corrupt_results(self):
        handler = object.__new__(SlopeKGHandler)
        handler.write_json = Mock()
        with patch('slopekg.server.PATHS', self.paths):
            self.assertTrue(handler._get_monitoring_baseline(urlparse('/api/monitoring/baseline')))
        self.assertEqual(handler.write_json.call_args.args[0]['status'], 'not_generated')
        self.assertFalse(handler._get_monitoring_baseline(urlparse('/api/unrelated')))

    def test_offline_report_embeds_source_text_safely_without_external_dependencies(self):
        import shutil
        report = build_baseline_experiment(self.paths)
        report['boundary'] = '</script><img src=x onerror=alert(1)>'
        self.paths.web_dir.mkdir()
        for name in ('pages.css', 'monitoring.css', 'api.js', 'monitoring-baseline.html', 'monitoring-baseline.js'):
            shutil.copyfile(Path(__file__).resolve().parents[1] / 'web' / name, self.paths.web_dir / name)
        destination = self.paths.output_dir / 'report.html'
        with patch('slopekg.monitoring_baseline.baseline_payload', return_value=report):
            export_baseline_report(destination, self.paths)
        content = destination.read_text(encoding='utf-8')
        self.assertIn('window.BASELINE_OFFLINE=', content)
        self.assertNotIn('src="./monitoring-baseline.js"', content)
        self.assertNotIn(report['boundary'], content)
        self.assertIn('\\u003c/script>', content)
