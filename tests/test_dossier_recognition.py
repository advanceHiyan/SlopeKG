from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import shutil
import unittest
from uuid import uuid4

from slopekg.config import PATHS
from slopekg.dossier_recognition import DossierConflict, adopt, available_runs, dossier, link
from slopekg.queries import find_slope
from slopekg.storage import write_json


class DossierRecognitionTest(unittest.TestCase):
    def setUp(self):
        temp = (Path.cwd() / ".tmp").resolve()
        root = temp / f"dossier_test_{uuid4().hex}"
        root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, root)
        output = root / "output"
        self.paths = replace(PATHS, root=root, output_dir=output, raw_dir=root / "raw", web_data_dir=output / "web")
        pdf = self.paths.raw_dir / "G209开放资料.pdf"
        pdf.parent.mkdir()
        pdf.write_bytes(b"%PDF synthetic fixture")
        digest = sha256(pdf.read_bytes()).hexdigest()
        self.link_request = dict(slope_id="s1", run_id="agent-test", document_id="doc", object_id="obj",
                                 actor="核对人", same_object_confirmed=True)
        run = output / "agent-recognition/runs/agent-test"
        self.page = run / "workspace/inputs/page.png"
        self.page.parent.mkdir(parents=True)
        self.page.write_bytes(b"synthetic page evidence")
        write_json(run / "workspace/manifest.json", {"documents": [{"document_id": "doc",
            "original_source_sha256": digest, "selected_physical_pages": [2], "files": [{
                "kind": "source_page_image", "physical_page": 2, "path": "inputs/page.png",
                "sha256": sha256(self.page.read_bytes()).hexdigest(),
            }],
        }]})
        fact = {"id": "f1", "object": "obj", "document_id": "doc", "field": "坡高", "value": "20～25",
                "unit": "m", "role": "observed", "model_status": "ok",
                "evidence": {"physical_page": 2, "quote": "边坡高度约为20～25m"}}
        write_json(run / "run.json", {"run_id": "agent-test", "run_directory": str(run), "processing_status": "partial",
            "documents": [{"id": "doc", "name": pdf.name, "path": str(pdf), "source_sha256": digest,
                "objects": [{"id": "obj", "document_id": "doc", "name": "K1+020-K1+040右侧边坡", "type": "边坡"}],
                "facts": [fact, {**fact, "id": "f2", "role": "recommended"}, {**fact, "id": "f3"}],
            }],
        })
        props = {"route_code_cache": "G209", "start_station_m": 1020, "end_station_m": 1040, "side": "右侧"}
        write_json(self.paths.graph_json, {"nodes": [
            {"id": "s1", "type": "Slope", "props": props},
            {"id": "s2", "type": "Slope", "props": {**props, "side": "左侧"}},
        ]})

    def adoption(self):
        state = link(self.paths, self.link_request)
        return dict(slope_id="s1", source_id=state["sources"][0]["id"], fact_ids=["f1"],
                    source_reviewed=True, actor="核对人", reason="原页核对一致", revision=state["revision"])

    def test_link_then_adopt_range_with_original_evidence_and_history(self):
        self.assertEqual(available_runs(self.paths)["runs"][0]["status"], "partial")
        request = self.adoption()
        self.assertNotIn("slope_height_raw", find_slope("s1", self.paths)["props"])
        facts = dossier(self.paths, "s1")["sources"][0]["facts"]
        self.assertEqual(facts["f1"]["evidence"][0]["page"], 2)
        self.assertIsNone(facts["f2"]["patch"])
        self.assertEqual(adopt(self.paths, request), {"updated": 1})
        slope = find_slope("s1", self.paths)["props"]
        self.assertEqual((slope["slope_height_min_m"], slope["slope_height_max_m"], slope["slope_height_raw"]),
                         (20, 25, "约20～25m"))
        self.assertIsNone(slope["slope_height_m"])
        state = dossier(self.paths, "s1")
        self.assertIsNone(state["history"][0]["changes"][0]["previous"]["slope_height_raw"])
        with self.assertRaises(DossierConflict):
            adopt(self.paths, request)
        self.assertEqual(adopt(self.paths, {**request, "revision": state["revision"]}), {"updated": 0})

    def test_reject_wrong_side_conflicting_candidates_and_changed_evidence(self):
        with self.assertRaisesRegex(ValueError, "左右侧"):
            link(self.paths, {**self.link_request, "slope_id": "s2"})
        request = self.adoption()
        with self.assertRaisesRegex(ValueError, "同一档案字段"):
            adopt(self.paths, {**request, "fact_ids": ["f1", "f3"]})
        self.page.write_bytes(b"changed")
        with self.assertRaisesRegex(OSError, "校验和"):
            adopt(self.paths, request)
        self.assertEqual(dossier(self.paths, "s1")["history"], [])
