"""Anonymous contracts for repeatable workflow acceptance checks."""
import copy
from hashlib import sha256
import unittest
from unittest.mock import Mock

from slopekg.workflow_check import run_workflow_checks
from scripts.check_workflow import LocalClient


class WorkflowCheckTests(unittest.TestCase):
    def payloads(self):
        return {
            "/api/documents": {"count": 0, "rows": []},
            "/api/graph": {"nodes": [], "edges": []},
            "/api/rules/library": {"publication_status": "not_generated", "execution_enabled": False},
            "/api/risk/rules": {"execution_enabled": False},
            "/api/multimodal/assets": {"count": 0, "assets": []},
            "/api/multimodal/quality": {"summary": {}},
            "/api/monitoring/catalog": {"status": "not_imported", "series": []},
            "/api/inspections/history/catalog": {"status": "not_imported", "count": 0},
            "/api/monitoring/baseline": {"status": "not_generated", "series": []},
        }

    def run_check(self, payloads, **kwargs):
        return run_workflow_checks(lambda p: copy.deepcopy(payloads[p]), Mock(return_value=b"original"), **kwargs)

    def by_id(self, result, key):
        return next(c for c in result["checks"] if c["id"] == key)

    def test_empty_sources_are_waiting_not_successful_acceptance(self):
        result = self.run_check(self.payloads())
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["counts"], {"waiting": 10, "passed": 1})
        self.assertFalse(result["exports"])

    def test_disconnected_server_does_not_hide_other_check_results(self):
        def fail(path):
            raise ConnectionError("local service unavailable")
        result = run_workflow_checks(fail, Mock())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["checks"]), 11)
        self.assertEqual(result["counts"], {"failed": 8, "waiting": 3})

    def test_unparsed_source_is_a_failure_even_if_old_graph_exists(self):
        payloads = self.payloads()
        payloads["/api/documents"] = {"count": 1, "rows": [{"file_name": "new.pdf", "parsed": False, "stale": False}]}
        result = self.run_check(payloads)
        self.assertEqual(self.by_id(result, "documents")["status"], "failed")
        self.assertIn("尚未解析", self.by_id(result, "documents")["detail"]["message"])

    def test_graph_broken_relationship_is_reported(self):
        payloads = self.payloads()
        payloads["/api/graph"] = {"nodes": [{"id": "s1", "type": "Slope"}],
                                  "edges": [{"source": "s1", "target": "missing"}]}
        result = self.run_check(payloads)
        self.assertIn("不存在的实体", self.by_id(result, "graph_risk")["detail"]["message"])

    def test_published_graph_and_screening_must_share_slope_and_source_versions(self):
        payloads = self.payloads()
        payloads["/api/documents"] = {"count": 1, "rows": [{"file_name": "sample.pdf", "parsed": True, "stale": False}]}
        slope = {"id": "s1", "type": "Slope"}
        payloads["/api/graph"] = {"nodes": [slope], "edges": [], "meta": {"pipeline": {"source_documents": [{"file_name": "sample.pdf", "sha256": "version1"}]}}}
        payloads["/api/parsed/documents"] = {"rows": [{"file_name": "sample.pdf", "source_sha256": "version1"}]}
        payloads["/api/slopes"] = {"count": 1, "rows": [slope]}
        payloads["/api/slopes/s1"] = {"slope": slope}
        payloads["/api/completeness"] = {"reports": [{"slope_id": "s1"}], "summary": {"ready": 0}}
        risk = {"formal_risk_assessment_enabled": False, "assessments": [{"slope_id": "s1", "formal_risk_level": None}]}
        payloads["/api/risk/screening"] = risk
        payloads["/api/risk/screening?slope_id=s1"] = risk
        payloads["/api/risk/evaluation"] = {"metrics": None}
        result = self.run_check(payloads)
        self.assertEqual(self.by_id(result, "documents")["status"], "passed")
        self.assertEqual(self.by_id(result, "graph_risk")["status"], "passed")
        payloads["/api/parsed/documents"]["rows"][0]["source_sha256"] = "version2"
        risk["formal_risk_assessment_enabled"] = True
        result = self.run_check(payloads)
        self.assertEqual(self.by_id(result, "documents")["status"], "failed")
        self.assertEqual(self.by_id(result, "graph_risk")["status"], "failed")

    def test_rule_execution_state_disagreement_is_not_candidate_success(self):
        payloads = self.payloads()
        payloads["/api/risk/rules"]["execution_enabled"] = True
        result = self.run_check(payloads)
        self.assertEqual(self.by_id(result, "rules")["status"], "failed")

    def test_mid_check_version_change_rejects_mixed_snapshot(self):
        payloads = self.payloads()
        calls = 0
        def get(path):
            nonlocal calls
            if path == "/api/graph":
                calls += 1
                if calls > 1:
                    return {"nodes": [], "edges": [], "version": "changed"}
            return copy.deepcopy(payloads[path])
        result = run_workflow_checks(get, Mock())
        self.assertEqual(self.by_id(result, "unchanged")["status"], "failed")

    def test_history_keeps_missing_date_and_cannot_pollute_recent_inspections(self):
        payloads = self.payloads()
        rows = [{"id": "h1", "asset_type": "桥梁", "observed_date": None,
                 "risk_eligible": False, "slope_id": None, "route_id": None}]
        payloads["/api/inspections/history/catalog"] = {"count": 1, "risk_eligible": False,
            "counts_by_type": {"桥梁": 1}, "invalid_date_rows": 1, "start": None, "end": None,
            "source_sha256": sha256(b"original").hexdigest()}
        payloads["/api/inspections/history?asset_type=all&include_undated=true&limit=100"] = {"rows": rows, "total": 1}
        payloads["/api/inspections"] = {"status": "reserved_not_connected", "rows": []}
        self.assertEqual(self.by_id(self.run_check(payloads), "history")["status"], "passed")
        payloads["/api/inspections"]["rows"] = rows
        self.assertEqual(self.by_id(self.run_check(payloads), "history")["status"], "failed")

    def test_explicit_exports_do_not_post_when_inputs_are_missing(self):
        post = Mock()
        result = self.run_check(self.payloads(), post_json=post)
        post.assert_not_called()
        self.assertFalse(result["exports"])

    def test_transport_rejects_external_urls_and_credentials(self):
        for url in ["https://example.com", "http://example.com", "http://user:password@localhost", "http://localhost/web/"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                LocalClient(url)
        client = LocalClient("http://127.0.0.1:8766")
        with self.assertRaises(ValueError):
            client.read("//example.com/")
