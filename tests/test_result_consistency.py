"""Anonymous regression cases for publication, cached sources and exports."""
import copy
import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from slopekg.exporter import export_standalone_html, export_risk_standalone_html
from slopekg.manual import save_slope_override
from slopekg.pipeline import DemoPipeline, graph_matches_documents, mark_cached_ocr_tasks, source_file_matches_record
from slopekg.risk_evaluation import evaluate_risk_screening
from slopekg.storage import write_json, write_jsonl
from tests.support import workspace_paths, sample_graph
from scripts import run_pipeline


class ResultConsistencyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.paths = workspace_paths(Path(directory.name))

    def payload(self, file, risk=False):
        element = "slopekg-risk-data" if risk else "slopekg-data"
        match = re.search(fr'<script id="{element}" type="application/json">([\s\S]*?)</script>', file.read_text(encoding="utf-8"))
        return json.loads(match.group(1))

    def test_missing_deep_graph_does_not_export_basic_as_deep(self):
        write_json(self.paths.graph_json, sample_graph())
        with self.assertRaises(FileNotFoundError):
            export_standalone_html(self.paths.output_dir / "deep.html", paths=self.paths, graph_mode="deep")

    def test_basic_export_uses_basic_metadata_and_latest_manual_values(self):
        write_json(self.paths.basic_graph_json, sample_graph())
        write_json(self.paths.basic_state_json, {"parse_mode": "basic"})
        write_json(self.paths.state_json, {"parse_mode": "deep"})
        write_json(self.paths.output_dir / "evaluation.basic.json", {"mode": "basic"})
        write_json(self.paths.evaluation_json, {"mode": "deep"})
        save_slope_override("s1", {"fields": {"slope_height_m": 42}}, self.paths)
        file = self.paths.output_dir / "basic.html"
        export_standalone_html(file, paths=self.paths, graph_mode="basic")
        payload = self.payload(file)
        self.assertEqual(payload["state"]["parse_mode"], "basic")
        self.assertEqual(payload["evaluation"]["mode"], "basic")
        self.assertEqual(payload["graph"]["nodes"][0]["props"]["slope_height_m"], 42)
        self.assertTrue(payload["completeness"].get("reports"))

    def test_risk_export_reflects_manual_edits_and_does_not_reuse_stale_scores(self):
        write_json(self.paths.graph_json, sample_graph())
        write_json(self.paths.output_dir / "risk" / "screening.json", {"assessments": [], "summary": {}})
        write_json(self.paths.output_dir / "risk" / "evaluation.json", {"metrics": {"accuracy": 1.0}})
        save_slope_override("s1", {"fields": {"slope_height_m": 42}}, self.paths)
        file = self.paths.output_dir / "risk.html"
        export_risk_standalone_html(file, paths=self.paths)
        payload = self.payload(file, risk=True)
        self.assertEqual(len(payload["risk"]["assessments"]), 1)
        self.assertIsNone(payload["evaluation"]["metrics"])

    def test_duplicate_expert_labels_are_rejected_instead_of_inflating_metrics(self):
        result = evaluate_risk_screening({"assessments": [
            {"slope_id": "s1", "screening_priority_code": "P1"},
            {"slope_id": "s2", "screening_priority_code": "P2"},
        ]}, {"labels": [
            {"slope_id": "s1", "expert_priority": "P1"},
            {"slope_id": "s1", "expert_priority": "P1"},
            {"slope_id": "s2", "expert_priority": "P2"},
        ]})
        self.assertEqual(result["evaluated"], 1)
        self.assertEqual(len(result["rejected"]), 2)
        self.assertEqual(result["metrics"]["urgent_recall_p1_p2"], 1.0)

    def test_same_size_and_mtime_do_not_hide_changed_source_hash(self):
        graph = {"meta": {"pipeline": {"source_documents": [{
            "file_name": "sample.pdf", "size_bytes": 20, "mtime_ns": 10, "sha256": "old",
        }]}}}
        documents = [{"file_name": "sample.pdf", "source_size_bytes": 20, "source_mtime_ns": 10, "source_sha256": "new"}]
        self.assertFalse(graph_matches_documents(graph, documents))

    def test_unchanged_stat_with_wrong_hash_does_not_reuse_old_parse(self):
        file = self.paths.root / "sample.pdf"
        file.write_bytes(b"new source")
        record = {"source_size_bytes": file.stat().st_size, "source_mtime_ns": file.stat().st_mtime_ns, "source_sha256": "old"}
        self.assertFalse(source_file_matches_record(file, record))

    def test_valid_parse_cache_is_reused(self):
        self.paths.raw_dir.mkdir(parents=True)
        file = self.paths.raw_dir / "sample.pdf"
        file.write_bytes(b"same source")
        write_jsonl(self.paths.parsed_dir / "documents.jsonl", [{"id": "doc", "file_name": file.name, "source_size_bytes": file.stat().st_size, "source_mtime_ns": file.stat().st_mtime_ns}])
        write_jsonl(self.paths.parsed_dir / "pages.jsonl", [{"id": "page", "document_id": "doc"}])
        self.assertEqual(DemoPipeline(self.paths).load_parsed_documents()["changed_document_ids"], [])

    def test_no_llm_flag_overrides_both_explicit_deep_options(self):
        for arguments in (("--mode", "deep", "--no-llm"), ("--deep", "--no-llm")):
            with self.subTest(arguments=arguments), patch("sys.argv", ["run_pipeline.py", *arguments]), patch("scripts.run_pipeline.DemoPipeline") as factory, redirect_stdout(StringIO()):
                factory.return_value.run.return_value = {}
                run_pipeline.main()
                self.assertEqual(factory.return_value.run.call_args.kwargs["parse_mode"], "basic")

    def test_missing_ocr_cache_is_retried_but_successful_blank_page_is_not(self):
        rows = mark_cached_ocr_tasks([
            {"id": "lost", "status": "done", "items": 3},
            {"id": "blank", "status": "done", "items": 0},
        ], [])
        self.assertEqual(rows[0]["status"], "pending")
        self.assertEqual(rows[1]["status"], "done")

    def test_deep_failure_keeps_the_completed_ocr_basic_result_active(self):
        pipeline = DemoPipeline(self.paths)
        parsed = {name: [] for name in ("documents", "pages", "text_blocks", "tables", "ocr_tasks")}
        parsed["documents"] = [{"id": "doc", "file_name": "sample.pdf"}]
        parsed["pages"] = [{"id": "page", "document_id": "doc"}]
        stages = []

        def build(*args, **kwargs):
            mode = kwargs.get("parse_mode")
            graph = sample_graph()
            graph["meta"]["test_stage"] = len(stages)
            stages.append((mode, kwargs.get("activate")))
            if kwargs.get("activate"):
                write_json(self.paths.graph_json, graph)
            return graph, {}

        def publish(*args, **kwargs):
            write_json(self.paths.state_json, {"publication_stage": kwargs.get("publication_stage"), "parse_mode": kwargs.get("parse_mode")})
            return {}

        with patch.object(pipeline, "parse_documents", return_value=copy.deepcopy(parsed)), patch.object(pipeline, "run_ocr", return_value=([], [])), patch.object(pipeline, "build_graph", side_effect=build), patch.object(pipeline, "publish_state", side_effect=publish), patch("slopekg.pipeline.extract_domain_candidates", return_value={}), patch("slopekg.pipeline.run_semantic_extraction", return_value=([], {})), patch("slopekg.pipeline.read_deepseek_key", return_value=None):
            with self.assertRaises(RuntimeError):
                pipeline.run(parse_mode="deep")
        active = json.loads(self.paths.graph_json.read_text(encoding="utf-8"))
        state = json.loads(self.paths.state_json.read_text(encoding="utf-8"))
        self.assertEqual(active["meta"]["test_stage"], 1)
        self.assertEqual(state["publication_stage"], "completed")
        self.assertEqual(state["parse_mode"], "basic")

    def test_reuse_parsed_rejects_modified_raw_pdf(self):
        self.paths.raw_dir.mkdir(parents=True)
        file = self.paths.raw_dir / "sample.pdf"
        file.write_bytes(b"new source")
        write_jsonl(self.paths.parsed_dir / "documents.jsonl", [{"id": "doc", "file_name": file.name, "source_size_bytes": 1, "source_mtime_ns": 0}])
        write_jsonl(self.paths.parsed_dir / "pages.jsonl", [{"id": "page", "document_id": "doc"}])
        with self.assertRaises(ValueError):
            DemoPipeline(self.paths).load_parsed_documents()
