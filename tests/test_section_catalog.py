"""Anonymous counterexamples for source-first subject reconciliation."""
import unittest

from slopekg.sections import numbered_headings, UNRESOLVED_SECTION
from slopekg.extractors import extract_domain_candidates, station_section_segments, extract_structural_planes
from slopekg.engineering import extract_engineering_tables
from slopekg.source_candidates import build_processing_audit


class SectionCatalogTests(unittest.TestCase):
    a, b = "K20+010-K20+110", "K21+200-K21+260"

    def block(self, name, page, text, y=100):
        return dict(id=name, document_id="anonymous", page=page, text=text,
                    bbox=[40, y, 450, y + 20], reading_order=y)

    def parsed(self, support=True):
        blocks = [self.block("old", 1, f"3.2.1 {self.a} 崩塌"),
                  self.block("new", 2, f"3.2.2 {self.b} 崩塌"),
                  self.block("description", 2, "该段边坡为岩质，坡高32m。", 130)]
        if support:
            blocks.append(self.block("caption", 3, f"图3.8 {self.b} 崩塌全貌"))
        return dict(documents=[dict(id="anonymous", kind="勘察报告", file_name="G777-survey.pdf")],
                    pages=[dict(document_id="anonymous", page=p, width=600) for p in range(1, 4)],
                    text_blocks=blocks, tables=[dict(id="inventory", document_id="anonymous", page=1,
                    bbox=[40, 20, 450, 70], rows=[["序号", "桩号", "灾害类型"], ["1", self.a, "崩塌"]])])

    def test_incomplete_inventory_is_supplemented_only_by_corroborated_heading(self):
        result = extract_domain_candidates(self.parsed())
        self.assertEqual({r["station"] for r in result["registry"]}, {self.a, self.b})
        added = next(r for r in result["registry"] if r["station"] == self.b)
        self.assertEqual(added["method"], "corroborated_numbered_section")
        self.assertEqual(added["evidence_block_ids"], ["new", "caption"])
        facts = next(r for r in result["section_facts"] if r["station"] == self.b)
        self.assertEqual(facts["slope_height_max_m"], 32)

    def test_unknown_heading_stops_old_subject_and_remains_a_review_candidate(self):
        result = extract_domain_candidates(self.parsed(False))
        self.assertEqual([r["station"] for r in result["registry"]], [self.a])
        self.assertTrue(any(h["resolution_status"] == "unconfirmed_heading" for h in result["section_resolution_issues"]))
        self.assertFalse(any(r.get("slope_height_max_m") == 32 for r in result["section_facts"]))
        rows, last = station_section_segments(f"前一处边坡高15m。\n3.2.2 {self.b} 崩塌\n边坡高32m。",
                                             {self.a}, self.a, {self.a})
        self.assertEqual(last, UNRESOLVED_SECTION)
        self.assertEqual(rows[0][0], self.a)
        self.assertIn("15m", rows[0][1])
        self.assertNotIn("32m", rows[0][1])
        rows, _ = station_section_segments("续页边坡高32m。", {self.a}, last, {self.a})
        self.assertEqual(rows, [])

    def test_contents_running_headers_and_same_page_ocr_are_not_corroboration(self):
        parsed = self.parsed(False)
        parsed["text_blocks"] += [self.block("ocr_duplicate", 2, f"3.2.2 {self.b} 崩塌"),
                                  self.block("toc", 3, f"3.2.2 {self.b} 崩塌.........23"),
                                  self.block("running", 3, f"G777 勘察报告 {self.b} 崩塌")]
        result = extract_domain_candidates(parsed)
        self.assertEqual(len(result["registry"]), 1)
        self.assertEqual(len([h for h in result["section_catalog"] if h["stations"] == [self.b]]), 1)
        self.assertEqual(numbered_headings("天然\n1.37\n稳定"), [])

    def test_invalid_inventory_is_retained_without_guessing_an_alias(self):
        parsed = self.parsed()
        wrong = "K22+200-K21+260"
        parsed["tables"][0]["rows"].append(["2", wrong, "崩塌"])
        result = extract_domain_candidates(parsed)
        self.assertEqual(result["registry_issues"][0]["station"], wrong)
        self.assertEqual(len(result["registry"]), 2)
        self.assertNotIn("resolved_alias", next(r for r in result["registry"] if r["station"] == self.b))

    def test_sibling_chapter_closes_subject_but_subsection_preserves_it(self):
        body = f"3.2.1 {self.a} 崩塌\n边坡高15m。\n3.2.1.1 稳定性分析\n边坡出现裂缝。\n3.3 区域说明\n区域边坡高80m。"
        rows, last = station_section_segments(body, {self.a}, None, {self.a})
        self.assertEqual(last, UNRESOLVED_SECTION)
        self.assertIn("裂缝", rows[0][1])
        self.assertNotIn("80m", rows[0][1])

    def test_unknown_table_heading_cannot_fall_back_to_previous_registered_slope(self):
        parsed = self.parsed(False)
        parsed["tables"].append(dict(id="factor", document_id="anonymous", page=2,
                                    bbox=[40, 160, 450, 200], rows=[["工况", "Fs"], ["天然", "1.37"]]))
        records = extract_engineering_tables(parsed, [dict(route_code="G777", station=self.a, document_id="anonymous")])
        self.assertIsNone(records[0]["station"])
        self.assertEqual(records[0]["station_candidates"], [self.b])
        self.assertIn("section_subject_not_registered", records[0]["quality_issues"])
        self.assertEqual(records[0]["stability_results"][0]["safety_factor"], 1.37)

    def test_scoped_rock_body_inside_one_root_is_not_a_new_slope_or_scope_barrier(self):
        body = "K20+050-K20+080"
        parsed = self.parsed(False)
        parsed["text_blocks"] = [self.block("intro", 1, "3.1 地质概况"),
                                  self.block("body", 2, f"3.2 {body} 危岩体（独立名称）稳定性分析"),
                                  self.block("pairs", 2, "危岩体裂隙产状175°∠76°。", 130)]
        parsed["tables"].append(dict(id="factor", document_id="anonymous", page=2,
                                    bbox=[40, 160, 450, 200], rows=[["工况", "Fs"], ["天然", "1.37"]]))
        result = extract_domain_candidates(parsed)
        heading = next(h for h in result["section_catalog"] if h["stations"])
        self.assertEqual(heading["resolution_status"], "scoped_body_subject")
        self.assertEqual(heading["parent_station"], self.a)
        self.assertEqual(result["engineering_records"][0]["station"], self.a)
        self.assertEqual(result["engineering_records"][0]["source_section_station_candidates"], [body])
        self.assertEqual(result["engineering_records"][0]["association_basis"], "contained_rock_body_section")
        self.assertEqual(len(result["registry"]), 1)

    def test_joint_direction_digits_are_not_consumed_as_a_plane_label(self):
        for direction in (11, 82, 236):
            planes = extract_structural_planes(f"发育主控结构面{direction}°∠72°。")
            self.assertEqual([(p["dip_direction"], p["dip_angle"]) for p in planes], [(direction, 72)])

    def test_later_cross_reference_after_same_page_sibling_is_not_corroboration(self):
        parsed = self.parsed()
        parsed["text_blocks"].append(self.block("sibling", 2, "3.3 其他地区崩塌概况", 150))
        result = extract_domain_candidates(parsed)
        self.assertEqual(len(result["registry"]), 1)
        self.assertTrue(any(h["stations"] == [self.b] and h["resolution_status"] == "unconfirmed_heading"
                            for h in result["section_resolution_issues"]))

    def test_body_range_containment_does_not_merge_an_independent_slope_heading(self):
        parsed = self.parsed()
        parsed["text_blocks"][1]["text"] = "3.2.2 K20+050-K20+080 崩塌"
        parsed["text_blocks"][3]["text"] = "图3.8 K20+050-K20+080 崩塌全貌"
        result = extract_domain_candidates(parsed)
        self.assertEqual(len(result["registry"]), 2)
        self.assertIsNone(next(h for h in result["section_catalog"] if h["resolution_status"] == "corroborated_heading")["parent_station"])

    def test_unresolved_subjects_and_missing_source_are_visible_in_processing_audit(self):
        parsed = self.parsed(False)
        result = extract_domain_candidates(parsed)
        result["slopes"].append(dict(station="K99+001-K99+101", route_code="G777", document_id="anonymous"))
        audit = build_processing_audit(parsed, result, {"nodes": [], "edges": []})
        self.assertFalse(audit["is_accuracy_measurement"])
        self.assertEqual(audit["summary"]["unconfirmed_heading"], 1)
        self.assertGreaterEqual(audit["summary"]["no_retained_section_source"], 1)


if __name__ == "__main__":
    unittest.main()
