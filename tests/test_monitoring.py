"""Independent ingestion, temporal, provenance and publication checks."""
from copy import deepcopy
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from openpyxl import Workbook

from slopekg.monitoring import (
    PILOT_FILES, build_huichang_pilot, export_monitoring_report, monitoring_catalog,
    monitoring_graph, monitoring_interfaces, observation_payload, replay_payload, source_file,
)
from slopekg.server import SlopeKGHandler
from tests.support import workspace_paths


def write_sample(path, header, rows):
    book = Workbook()
    sheet = book.active
    sheet.title = "记录"
    sheet.append(header)
    for row in rows:
        sheet.append(row)
    book.save(path)
    book.close()


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = workspace_paths(Path(self.temp.name))
        self.source = self.paths.root / "data/202610/监测数据/会昌"
        self.source.mkdir(parents=True)
        for name in PILOT_FILES:
            if "土壤含水率" in name:
                header = ["序号", "时间", "埋深:无(%)"]
                rows = [[2, "2026-05-27 18:00:00", "0"], [1, "2026-05-25 18:00:00", "20.5"]]
            elif "雨量" in name:
                header = ["序号", "设备名称", "时间", "单日雨量值", "累计雨量值"]
                rows = [[2, "source", "2026-05-27", 0, 4], [1, "source", "2026-05-25", 4, 4]]
            else:
                header = ["序号", "设备名称", "时间", "数值"]
                rows = [[2, name.split("(")[0], "2026-05-27", "3,-1"], [1, name.split("(")[0], "2026-05-25", "1,0"]]
            write_sample(self.source / name, header, rows)
        self.catalog = build_huichang_pilot(self.paths)

    def meta(self, category):
        return next(s for s in self.catalog["series"] if s["category"] == category)

    def test_counts_raw_values_units_and_source_cells(self):
        self.assertEqual(self.catalog["observations"], 16)
        self.assertEqual(self.catalog["common_days"], 2)
        meta = self.meta("地表位移")
        payload = observation_payload({"series_id": [meta["id"]], "start": ["2026-05-25"], "end": ["2026-05-27"]}, self.paths)
        self.assertEqual([r["raw_values"]["数值"] for r in payload["rows"]], ["1,0", "3,-1"])
        self.assertEqual(payload["rows"][0]["source"]["row"], 3)
        self.assertEqual(payload["rows"][0]["source"]["cells"]["数值"], "D3")
        self.assertEqual(payload["rows"][0]["components"], [1, 0])
        self.assertIsNone(meta["unit"])
        self.assertIsNone(meta["slope_id"])
        self.assertEqual(self.meta("土壤含水率")["unit"], "%")
        rain = observation_payload({"series_id": [self.meta("雨量")["id"]]}, self.paths)["rows"][0]
        self.assertEqual(rain["raw_values"], {"单日雨量值": 4, "累计雨量值": 4})

    def test_replay_keeps_calendar_gaps_zeros_and_cutoff(self):
        data = replay_payload({"start": ["2026-05-25"], "end": ["2026-05-27"]}, self.paths)
        self.assertEqual(data["dates"], ["2026-05-25", "2026-05-26", "2026-05-27"])
        rain = next(s for s in data["series"] if s["meta"]["category"] == "雨量")
        self.assertEqual(rain["points"][0]["components"], [4])
        self.assertIsNone(rain["points"][1]["components"])
        self.assertEqual(rain["points"][2]["components"], [0])
        early = replay_payload({"start": ["2026-05-25"], "end": ["2026-05-25"]}, self.paths)
        self.assertTrue(all(len(s["points"]) == 1 and s["record_count"] == 1 for s in early["series"]))
        self.assertFalse(early["risk_eligible"])

    def test_duplicate_and_invalid_values_are_preserved_but_not_plotted(self):
        target = self.source / PILOT_FILES[1]
        write_sample(target, ["序号", "设备名称", "时间", "数值"], [
            [1, "a", "2026-05-25", "1,0"], [2, "a", "2026-05-25", "2,0"],
            [3, "a", "2026-05-27", "NaN,0"], [4, "a", "bad-date", "4,0"],
            [5, "a", "2026-05-28", "=1+2"],
        ])
        self.catalog = build_huichang_pilot(self.paths)
        meta = self.meta("地表位移")
        self.assertEqual(meta["row_count"], 5)
        self.assertEqual(meta["duplicate_time_rows"], 2)
        self.assertEqual(meta["invalid_time_rows"], 1)
        self.assertEqual(meta["invalid_value_rows"], 2)
        data = replay_payload({"start": ["2026-05-25"], "end": ["2026-05-28"]}, self.paths)
        item = next(s for s in data["series"] if s["meta"]["id"] == meta["id"])
        self.assertTrue(all(p["components"] is None for p in item["points"]))
        raw = observation_payload({"series_id": [meta["id"]], "end": ["2026-05-28"], "include_invalid_time": ["true"]}, self.paths)
        self.assertEqual(raw["total"], 5)
        self.assertTrue(any(r["raw_values"]["数值"] == "=1+2" for r in raw["rows"]))
        self.assertTrue(any("invalid_time" in r["quality_flags"] for r in raw["rows"]))

    def test_pagination_and_query_validation(self):
        query = {"series_id": [self.meta("雨量")["id"]], "limit": ["1"], "offset": ["1"]}
        self.assertEqual(observation_payload(query, self.paths)["rows"][0]["components"], [0])
        for bad in ({"start": ["2026-05-28"], "end": ["2026-05-25"]}, {"limit": ["1001"]}, {"offset": ["-1"]}, {"start": ["bad"]}, {"include_invalid_time": ["yes"]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                observation_payload(bad, self.paths)
        with self.assertRaises(KeyError):
            observation_payload({"series_id": ["' OR 1=1"]}, self.paths)

    def test_reimport_has_no_duplicate_records_and_missing_file_does_not_publish(self):
        repeat = build_huichang_pilot(self.paths)
        self.assertEqual(repeat["batch_id"], self.catalog["batch_id"])
        self.assertEqual(repeat["observations"], 16)
        self.assertEqual(observation_payload({}, self.paths)["total"], 2)
        before = monitoring_catalog(self.paths)
        (self.source / PILOT_FILES[0]).unlink()
        with self.assertRaises(FileNotFoundError):
            build_huichang_pilot(self.paths)
        self.assertEqual(monitoring_catalog(self.paths), before)

    def test_source_checksum_and_provenance_graph(self):
        meta = self.meta("雨量")
        self.assertEqual(source_file(meta["id"], self.paths), self.source / meta["file_name"])
        with self.assertRaises(KeyError):
            source_file("../../AGENTS.md", self.paths)
        write_sample(self.source / meta["file_name"], ["时间", "数值"], [["2026-05-25", 999]])
        with self.assertRaises(ValueError):
            source_file(meta["id"], self.paths)
        graph = monitoring_graph(self.paths)
        ids = {n["id"] for n in graph["nodes"]}
        self.assertEqual(len(ids), 34)
        self.assertEqual(len(graph["edges"]), 48)
        self.assertTrue(all(e["source"] in ids and e["target"] in ids for e in graph["edges"]))
        self.assertFalse(any(n["type"] == "Slope" for n in graph["nodes"]))

    def test_interfaces_update_only_monitoring_scope(self):
        original = {"interfaces": [{"path": "/api/monitoring/observations", "status": "reserved_not_connected"}, {"path": "/api/environment/latest", "status": "reserved_not_connected"}]}
        before = deepcopy(original)
        result = monitoring_interfaces(original, self.paths)
        self.assertEqual(original, before)
        self.assertEqual(result["interfaces"][0]["status"], "implemented_pilot")
        self.assertEqual(result["interfaces"][1]["status"], "reserved_not_connected")

    def test_modified_database_is_rejected_before_returning_observations(self):
        database = self.paths.monitoring_dir / self.catalog["database"]
        # Keep a readable database but alter stored raw evidence after publication.
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("UPDATE observations SET payload='{}'")
        with self.assertRaises(OSError):
            observation_payload({}, self.paths)

    def test_missing_pilot_does_not_break_the_other_interface_catalog(self):
        (self.paths.monitoring_dir / self.catalog["database"]).unlink()
        original = {"interfaces": [
            {"path": "/api/monitoring/observations", "status": "reserved_not_connected"},
            {"path": "/api/environment/latest", "status": "reserved_not_connected"},
        ]}
        result = monitoring_interfaces(original, self.paths)
        self.assertEqual(result["interfaces"][0]["status"], "data_unavailable")
        self.assertEqual(result["interfaces"][1], original["interfaces"][1])

    def test_http_routes_reach_pilot_and_reject_bad_query(self):
        with patch("slopekg.server.PATHS", self.paths):
            for path, status in [("/api/monitoring/replay?start=2026-05-25&end=2026-05-27", None), ("/api/monitoring/observations?limit=0", 400), ("/api/monitoring/observations?series_id=unknown", 404)]:
                handler = object.__new__(SlopeKGHandler)
                handler.path = path
                handler.pipeline = Mock()
                handler.write_json = Mock()
                handler.do_GET()
                payload = handler.write_json.call_args.args[0]
                if status:
                    self.assertEqual(handler.write_json.call_args.kwargs["status"], status)
                else:
                    self.assertEqual(payload["dates"][-1], "2026-05-27")

    def test_offline_report_embeds_data_safely(self):
        self.paths.web_dir.mkdir()
        project_web = Path(__file__).resolve().parents[1] / "web"
        for name in ("monitoring.html", "monitoring.js", "monitoring.css", "pages.css", "api.js"):
            (self.paths.web_dir / name).write_bytes((project_web / name).read_bytes())
        output = self.paths.output_dir / "share/pilot.html"
        result = export_monitoring_report(output, {"start": ["2026-05-25"], "end": ["2026-05-27"]}, self.paths)
        html = output.read_text(encoding="utf-8")
        self.assertEqual(result["series"], 8)
        self.assertIn("window.MONITORING_OFFLINE=", html)
        self.assertNotIn('src="./monitoring.js"', html)
        self.assertIn("离线仅含每个日期最后一条代表记录", html)


if __name__ == "__main__":
    unittest.main()
