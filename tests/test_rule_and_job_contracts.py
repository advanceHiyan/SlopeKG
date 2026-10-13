"""Rule, input and job contracts checked without private documents or services."""
from http import HTTPStatus
from pathlib import Path
import copy
import tempfile
import unittest
from unittest.mock import Mock, patch

from slopekg.jobs import PipelineJobManager, PdfDirectoryWatcher
from slopekg.manual import apply_slope_overrides, normalize_rule_value, save_manual_rule, save_slope_override
from slopekg.queries import document_inventory
from slopekg.risk import evaluate_condition
from slopekg.server import SlopeKGHandler
from slopekg.storage import write_json
from tests.support import workspace_paths, sample_graph


class RuleAndJobContractTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.paths = workspace_paths(Path(directory.name))

    def rule(self, value, operator="gte"):
        return save_manual_rule({
            "title": "匿名阈值规则", "approval_status": "approved", "priority": "P1",
            "conditions": [{"field": "slope_height_m", "operator": operator, "value": value}],
        }, self.paths)

    def test_zero_rule_threshold_is_kept_and_executable(self):
        rule = self.rule(0)
        self.assertTrue(rule["execution_enabled"])
        self.assertEqual(rule["conditions"][0]["value"], "0")

    def test_membership_values_keep_zero_and_false(self):
        self.assertEqual(normalize_rule_value("in", [0, False, 1]), ["0", "False", "1"])

    def test_nonfinite_rule_thresholds_are_stored_without_execution(self):
        for value in ("NaN", "inf", "-inf", "1e400"):
            with self.subTest(value=value):
                rule = self.rule(value)
                self.assertFalse(rule["execution_enabled"])
                self.assertEqual(rule["execution_status"], "stored_not_executable")

    def test_missing_values_do_not_match_text_or_membership_conditions(self):
        for missing in (None, "", [], {}, (), set()):
            for operator, expected in (("eq", "None"), ("eq", ""), ("in", ["None", ""]), ("exists", True)):
                with self.subTest(missing=missing, operator=operator):
                    self.assertFalse(evaluate_condition(missing, operator, expected))
        self.assertTrue(evaluate_condition(0, "exists", True))
        self.assertTrue(evaluate_condition(0, "contains", "0"))

    def test_override_count_only_counts_matching_slopes(self):
        graph = sample_graph()
        original = copy.deepcopy(graph)
        for slope_id in ("s1", "removed"):
            save_slope_override(slope_id, {"fields": {"slope_height_m": 42}}, self.paths)
        result = apply_slope_overrides(graph, self.paths)
        self.assertEqual(result["meta"]["manual_override_count"], 1)
        self.assertEqual(graph, original)

    def test_text_boolean_options_are_rejected_before_starting_jobs(self):
        cases = [("/api/pipeline/jobs", name) for name in ("force_ocr", "reuse_parsed", "use_llm")]
        cases += [("/api/rules/jobs", name) for name in ("use_llm", "force_ocr")]
        for path, name in cases:
            with self.subTest(path=path, name=name), patch("slopekg.server.JOB_MANAGER") as jobs, patch("slopekg.server.RULE_JOB_MANAGER") as rules:
                handler = object.__new__(SlopeKGHandler)
                handler.path = path
                handler.read_json_body = Mock(return_value={name: "false"})
                handler.request_origin_allowed = Mock(return_value=True)
                handler.write_json = Mock()
                handler.do_POST()
                self.assertEqual(handler.write_json.call_args.kwargs["status"], HTTPStatus.BAD_REQUEST)
                jobs.start.assert_not_called()
                rules.start.assert_not_called()

    def test_thread_start_failure_does_not_leave_a_permanent_queued_job(self):
        manager = PipelineJobManager(Mock())
        with patch("slopekg.jobs.threading.Thread") as thread:
            thread.return_value.start.side_effect = RuntimeError("thread capacity reached")
            first, _ = manager.start({})
            self.assertEqual(first["status"], "failed")
            self.assertIsNotNone(first["finished_at"])
            thread.return_value.start.side_effect = None
            second, created = manager.start({})
            self.assertTrue(created)
            self.assertNotEqual(first["id"], second["id"])

    def test_job_options_are_owned_by_the_job(self):
        manager = PipelineJobManager(Mock())
        options = {"ocr_pages": 0}
        with patch("slopekg.jobs.threading.Thread"):
            job, _ = manager.start(options)
        options["ocr_pages"] = 1000
        self.assertEqual(manager.get(job["id"])["options"]["ocr_pages"], 0)

    def test_risk_evaluation_endpoint_tracks_current_manual_rules(self):
        write_json(self.paths.graph_json, sample_graph())
        write_json(self.paths.root / "data" / "validation" / "risk_gold.json", {"labels": [{"slope_id": "s1", "expert_priority": "P4"}]})
        write_json(self.paths.output_dir / "risk" / "evaluation.json", {"metrics": {"exact_priority_accuracy": 1.0}})
        save_slope_override("s1", {"fields": {"slope_height_m": 42}}, self.paths)
        self.rule(10)
        handler = object.__new__(SlopeKGHandler)
        handler.path = "/api/risk/evaluation"
        handler.write_json = Mock()
        with patch("slopekg.server.PATHS", self.paths):
            handler.do_GET()
        result = handler.write_json.call_args.args[0]
        self.assertEqual(result["metrics"]["exact_priority_accuracy"], 0.0)

    def test_inventory_and_watcher_ignore_directories_named_pdf(self):
        self.paths.raw_dir.mkdir(parents=True)
        (self.paths.raw_dir / "folder.pdf").mkdir()
        (self.paths.raw_dir / "sample.PDF").write_bytes(b"%PDF-sample")
        inventory = document_inventory(self.paths)
        watcher = PdfDirectoryWatcher(Mock(), paths=self.paths)
        self.assertEqual([row["file_name"] for row in inventory["rows"]], ["sample.PDF"])
        self.assertEqual([row[0] for row in watcher.snapshot()], ["sample.PDF"])
