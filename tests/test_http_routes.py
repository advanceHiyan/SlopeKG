"""HTTP dispatch contracts, using anonymous data and no running server."""
from http import HTTPStatus
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from slopekg.server import SlopeKGHandler
from tests.support import workspace_paths


class HttpRouteTests(unittest.TestCase):
    def handler(self, path):
        handler = object.__new__(SlopeKGHandler)
        handler.path = path
        handler.pipeline = Mock()
        handler.write_json = Mock()
        handler.read_json_body = Mock(return_value={})
        handler.request_origin_allowed = Mock(return_value=True)
        return handler

    def test_latest_job_route_is_not_treated_as_a_job_id(self):
        for prefix, manager_name in [("pipeline", "JOB_MANAGER"), ("rules", "RULE_JOB_MANAGER")]:
            with self.subTest(prefix=prefix), patch(f"slopekg.server.{manager_name}") as manager:
                manager.latest.return_value = None
                handler = self.handler(f"/api/{prefix}/jobs/latest")
                handler.do_GET()
                handler.write_json.assert_called_once_with({"status": "none"})
                manager.get.assert_not_called()

    def test_unknown_job_keeps_404_response(self):
        with patch("slopekg.server.JOB_MANAGER") as manager:
            manager.get.return_value = None
            handler = self.handler("/api/pipeline/jobs/missing")
            handler.do_GET()
            handler.write_json.assert_called_once_with({"error": "unknown job: missing"}, status=HTTPStatus.NOT_FOUND)

    def test_risk_query_filters_assessments_without_losing_summary(self):
        payload = {"summary": {"count": 2}, "assessments": [{"slope_id": "a"}, {"slope_id": "b"}]}
        with patch("slopekg.server.current_risk_screening", return_value=payload):
            handler = self.handler("/api/risk/screening?slope_id=b")
            handler.do_GET()
        handler.write_json.assert_called_once_with({"summary": {"count": 2}, "assessments": [{"slope_id": "b"}]})
        self.assertEqual(len(payload["assessments"]), 2)

    def test_unknown_parsed_table_is_404(self):
        handler = self.handler("/api/parsed/unknown")
        handler.pipeline.parsed_table.side_effect = KeyError("unknown")
        handler.do_GET()
        handler.write_json.assert_called_once_with({"error": "unknown parsed table: unknown"}, status=HTTPStatus.NOT_FOUND)

    def test_missing_catalog_has_explicit_empty_state(self):
        with tempfile.TemporaryDirectory() as directory, patch("slopekg.server.PATHS", workspace_paths(Path(directory))):
            handler = self.handler("/api/multimodal/assets")
            handler.do_GET()
        handler.write_json.assert_called_once_with({"status": "not_generated", "count": 0, "assets": []})

    def test_foreign_origin_rejected_before_reading_mutation_body(self):
        handler = self.handler("/api/pipeline/jobs")
        handler.request_origin_allowed.return_value = False
        handler.do_POST()
        handler.read_json_body.assert_not_called()
        handler.write_json.assert_called_once_with({"error": "cross-origin request denied"}, status=HTTPStatus.FORBIDDEN)

    def test_queued_job_and_busy_job_keep_202_and_409(self):
        for created, status in [(True, HTTPStatus.ACCEPTED), (False, HTTPStatus.CONFLICT)]:
            with self.subTest(created=created), patch("slopekg.server.JOB_MANAGER") as manager:
                manager.start.return_value = ({"id": "job"}, created)
                handler = self.handler("/api/pipeline/jobs")
                handler.do_POST()
                handler.write_json.assert_called_once_with({"id": "job"}, status=status)

    def test_unknown_post_does_not_execute_pipeline(self):
        handler = self.handler("/api/unknown")
        handler.do_POST()
        handler.pipeline.run.assert_not_called()
        handler.write_json.assert_called_once_with({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
