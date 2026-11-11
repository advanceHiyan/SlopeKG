"""Exercise cross-document assessment with anonymous local source bundles."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from slopekg.config import PATHS
from slopekg.standard_assessment.contract import CONTRACT_VERSION, enumeration, fact_record, number
from slopekg.standard_assessment.service import StandardAssessmentService
from slopekg.standard_assessment.workspace_cases import compose_case


SLOPE = {"id": "slope-example", "label": "演示滑坡"}
CURRENT = "2026-11-09"


def source_bundle(name, specifications, *, day=CURRENT, scenario="natural", workspace="debug"):
    path = f"example/{name}.pdf"
    sha = "a" * 64
    scope = {
        "subject": {"source_object_ref": f"{name}/body", "object_kind": "landslide_body"},
        "time": {"kind": "instant" if day else "unknown", "start": day, "end": day,
                 "raw": f"{name}报告时间", "basis_fact_ids": [f"{name}-date"]},
        "scenario": {"code": scenario, "raw": f"{name}报告工况", "basis_refs": [f"{name}-scenario"]},
    }
    facts, records, contexts = [], [], []
    for idx, (variable, value, options) in enumerate(specifications):
        sid, cid = f"source-{name}-{idx}", f"context-{name}-{idx}"
        records.append({"record_ref_id": sid, "source_path": path, "snapshot_sha256": sha})
        contexts.append({"context_id": cid, "source_path": path, "source_sha256": sha,
                         "spans": [{"file_page_1_based": 1, "quote": f"{name}报告原文{idx}"}]})
        facts.append(fact_record(variable, value, options.get("scope", scope), source_refs=[sid], evidence_refs=[cid],
                                 origin=options.get("origin", "agent"), support=options.get("support", "model_supported"),
                                 role=options.get("role", "observation"), source_standard=options.get("standard", "GB/T 40112-2021")))
        facts[-1]["basis_status"] = "conditional"
    return {
        "workspace": workspace,
        "case": {"case_id": name, "target_ref": scope["subject"]["source_object_ref"], "scope": scope,
                 "resolution_version": 1, "hazard_type_supported": True, "source_hashes": {path: sha},
                 "recognition_lineage": {"source_name": f"{name}.pdf"}},
        "link": {"link_id": f"link-{name}"}, "facts": facts, "source_records": records, "contexts": contexts,
    }


def development(options=None):
    return "development", enumeration("development", "强发育"), options or {}


def population(count, options=None):
    return "threatened_people", number(count, "person"), options or {}


class AssessmentWorkspaceCompositionTests(unittest.TestCase):
    def setUp(self):
        temporary_root = PATHS.root / ".tmp"
        temporary_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temporary_root, prefix="workspace-composition-")
        self.addCleanup(self.temp.cleanup)
        paths = replace(PATHS, output_dir=Path(self.temp.name))
        self.api = StandardAssessmentService(paths, database=paths.output_dir / "workspace.sqlite")

    def test_join_complementary_documents_and_keep_original_proof(self):
        survey = source_bundle("survey", [development()], workspace="main")
        census = source_bundle("census", [population(18)])
        original = deepcopy([survey, census])
        for source in original:
            result = compose_case(self.api, SLOPE, [source])["results"][-1]
            self.assertIsNone(result["table17_result"]["grade"])
        made = compose_case(self.api, SLOPE, [survey, census], "survey")
        result = made["results"][-1]
        self.assertEqual(result["table17_result"]["grade"], "危险性大")
        self.assertEqual(result["basis_status"], "conditional")
        self.assertEqual(result["review_status"], "pending")
        self.assertFalse(result["formal_assessment_enabled"])
        self.assertEqual(made["case"]["workspace_slope_id"], SLOPE["id"])
        self.assertEqual({row["case_id"] for row in made["case"]["workspace_sources"]}, {"survey", "census"})
        self.assertEqual({row["source_path"] for row in made["contexts"]}, {"example/survey.pdf", "example/census.pdf"})
        self.assertEqual({row["status"] for row in made["case"]["workspace_fact_trace"]}, {"included"})
        source_ids = {row["source_fact_id"] for row in made["case"]["workspace_fact_trace"]}
        self.assertEqual(source_ids, {source["facts"][0]["fact_id"] for source in original})
        self.assertEqual([survey, census], original)

    def test_period_and_scenario_must_match_and_be_known(self):
        survey = source_bundle("survey", [development()])
        for kwargs, message in [({"day": "2026-11-08"}, "相同的明确时期"), ({"day": None}, "相同的明确时期"),
                                ({"scenario": "rainstorm"}, "相同的明确工况"),
                                ({"scenario": "unspecified"}, "相同的明确工况")]:
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, message):
                compose_case(self.api, SLOPE, [survey, source_bundle("census", [population(18)], **kwargs)])
        self.assertEqual(self.api.store.rows("assessment_case"), [])

    def test_no_adoption_of_unknown_or_other_scope_or_reference_inputs(self):
        survey = source_bundle("survey", [development()])
        census = source_bundle("census", [population(n) for n in [9, 18, 19, 20, 21, 22]])
        fact = census["facts"]
        fact[0]["scope"]["subject"].update(object_kind="section", source_object_ref="census/section")
        fact[1]["scope"]["time"].update(kind="unknown", start=None, end=None)
        fact[2]["scope"]["scenario"]["code"] = "rainstorm"
        fact[3]["role"] = "prediction"
        fact[5]["source_check"]["content"] = "unchecked"
        # Two differently scoped original facts may point to the same quote.
        fact[0]["evidence_refs"] = fact[4]["evidence_refs"]
        made = compose_case(self.api, SLOPE, [survey, census])
        self.assertIsNone(made["results"][-1]["table17_result"]["grade"])
        trace = {row["source_fact_id"]: row for row in made["case"]["workspace_fact_trace"]}
        for index, reason in enumerate(["different_or_unresolved_object", "period_unresolved", "different_scenario", "reference_role"]):
            row = trace[fact[index]["fact_id"]]
            self.assertEqual(row["status"], "excluded")
            self.assertIn(reason, row["reasons"])
        self.assertEqual(trace[fact[4]["fact_id"]]["status"], "requires_review")
        self.assertIn("ambiguous_evidence_scope", trace[fact[4]["fact_id"]]["reasons"])
        self.assertNotIn(fact[4]["evidence_refs"][0], made["case"]["workspace_evidence"])
        self.assertEqual(trace[fact[5]["fact_id"]]["status"], "requires_review")
        self.assertIn(fact[5]["evidence_refs"][0], made["case"]["workspace_evidence"])
        self.assertIn(survey["contexts"][0]["context_id"], made["case"]["workspace_evidence"])

    def test_unknown_legacy_scope_remains_readable_and_ungraded(self):
        legacy = source_bundle("legacy", [development(), population(18)], day=None, scenario="unspecified", workspace="main")
        made = compose_case(self.api, SLOPE, [legacy])
        self.assertEqual(made["case"]["scope"]["time"]["kind"], "unknown")
        self.assertEqual(made["results"][-1]["workflow_status"], "needs_evidence")
        self.assertEqual(len(made["contexts"]), 2)
        self.assertEqual(made["case"]["workspace_evidence"], {})

    def test_selected_condition_only_adopts_facts_explicitly_in_scope(self):
        source = source_bundle("survey", [development(), population(18), population(19)], scenario="unspecified")
        for fact, code in zip(source["facts"], ("natural", "rainstorm", "unspecified")):
            fact["scope"] = deepcopy(fact["scope"])
            fact["scope"]["scenario"]["code"] = code
        made = compose_case(self.api, SLOPE, [source], selected_scenario="natural")
        self.assertEqual([fact["variable_code"] for fact in made["facts"]], ["development"])
        self.assertEqual([row["reasons"] for row in made["case"]["workspace_fact_trace"]],
                         [[], ["different_scenario"], ["scenario_unresolved"]])
        self.assertEqual(len(made["case"]["workspace_evidence"]), 1)

    def test_conflict_and_replay_preserve_choice_and_history(self):
        survey = source_bundle("survey", [development(), population(9)])
        census = source_bundle("census", [population(18)])
        made = compose_case(self.api, SLOPE, [survey, census], "survey")
        result = made["results"][-1]
        self.assertIsNone(result["table17_result"]["grade"])
        self.assertEqual(result["workflow_status"], "needs_resolution")
        chosen = next(fact for fact in made["facts"] if fact["variable_code"] == "threatened_people" and fact["value"]["value"] == 18)
        request = {"contract_version": CONTRACT_VERSION, "case_id": made["case"]["case_id"], "expected_resolution_version": 1}
        self.api.review_assessment({**request, "actor_kind": "human", "reviewer_ref": "单元测试复核人",
                                    "reason": "核对来源后选择统计口径", "decision": "select_branch", "target_id": chosen["fact_id"]})
        replay = compose_case(self.api, SLOPE, [census, survey], "census")
        self.assertEqual(replay["case"]["case_id"], made["case"]["case_id"])
        self.assertEqual(replay["case"]["resolution_version"], 2)
        self.assertEqual(replay["case"]["selections"]["threatened_people"], chosen["fact_id"])
        self.assertEqual(replay["results"][0]["result_id"], result["result_id"])
        self.assertEqual(replay["results"][-1]["table17_result"]["grade"], "危险性大")
        self.assertEqual(len(self.api.store.rows("assessment_case")), 1)

    def test_no_pooling_stability_coefficients_between_documents(self):
        survey = source_bundle("survey", [("fs", number(1.1, "1"), {})])
        calc = source_bundle("calc", [("fst", number(1.15, "1"), {})])
        made = compose_case(self.api, SLOPE, [survey, calc])
        self.assertEqual(made["results"][-1]["partials"]["stability_axes"], [])

    def test_link_guard_and_failed_evaluation_leave_no_partial_records(self):
        survey = source_bundle("survey", [development()])

        def inactive(_db):
            raise ValueError("来源关联已撤销")

        with self.assertRaisesRegex(ValueError, "来源关联已撤销"):
            compose_case(self.api, SLOPE, [survey], guard=inactive)
        with patch.object(self.api, "evaluate_assessment", side_effect=ValueError("计算中断")), self.assertRaisesRegex(ValueError, "计算中断"):
            compose_case(self.api, SLOPE, [survey])
        self.assertTrue(all(not rows for rows in self.api.store.export().values()))

    def test_test_and_operational_sources_cannot_be_combined(self):
        survey = source_bundle("survey", [development()])
        census = {**source_bundle("census", [population(18)]), "test_only": True}
        with self.assertRaisesRegex(ValueError, "测试资料和业务资料"):
            compose_case(self.api, SLOPE, [survey, census])
