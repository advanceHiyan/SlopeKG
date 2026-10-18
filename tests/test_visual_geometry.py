"""Geometry and workflow regressions, not model recognition quality scores."""
from pathlib import Path
import copy
import io
import tempfile
import unittest

import fitz
from PIL import Image, ImageDraw

from slopekg.visual_contract import LAYOUT, READ, VERIFY, reading, ContractError
from slopekg.visual_document import VisualDocument
from slopekg.visual_geometry import contain_source_cells, recovery_bounds, image_line_cells
from slopekg.visual_pipeline import VisualPipeline, VisualSettings
from tests.test_visual_pipeline import ScriptedModel, make_pdf, source_read


class CropGeometryTests(unittest.TestCase):
    def test_partial_text_row_is_preserved_without_swallowing_other_column(self):
        cells=[{"bbox":[.1,.1,.49,.14]},{"bbox":[.50,.1,.9,.14]}]
        result=contain_source_cells([0,.12,.505,.13],cells)
        self.assertEqual(result,[0,.1,.505,.14])
        self.assertEqual(result,contain_source_cells(result,cells))

    def test_boundary_recovery_reads_past_parent_and_keeps_neighbouring_column_out(self):
        parent=[.1,.1,.45,.4]
        cells=[{"bbox":[.1,.42,.45,.47]},{"bbox":[.55,.405,.95,.44]}]
        result=recovery_bounds(parent,[.1,.37,.45,.4],cells,["bottom"])
        self.assertEqual(result,[.1,.1,.45,.47])
        self.assertLessEqual(result[0],parent[0])
        self.assertGreater(result[3],parent[3])

    def test_no_cells_fallback_expands_source_context_instead_of_shrinking_to_fragment(self):
        parent=[.2,.2,.8,.6]
        result=recovery_bounds(parent,[.2,.55,.8,.6],[],["bottom"])
        self.assertAlmostEqual(result[3],.8)
        self.assertEqual(result[:3],parent[:3])
        self.assertEqual(recovery_bounds(parent,parent,[],["right","bottom","left","top"]),
                         recovery_bounds(parent,parent,[],["top","left","bottom","right"]))

    def test_raster_lines_are_dpi_independent_and_do_not_assume_column_count(self):
        with tempfile.TemporaryDirectory() as directory:
            results=[]
            for factor in (1,2,3):
                img=Image.new("L",(400*factor,300*factor),255);draw=ImageDraw.Draw(img)
                for y in (30,100,170):
                    for start in (20,240):
                        for x in range(start,start+100,12):
                            draw.rectangle([x*factor,y*factor,(x+7)*factor,(y+12)*factor],fill=0)
                path=Path(directory)/f"scale{factor}.png";img.save(path)
                cells=image_line_cells(path)
                self.assertEqual(len(cells),6)
                self.assertTrue(all(b[2]-b[0]<.4 for b in cells))
                results.append(cells)
            for cells in results[1:]:
                self.assertTrue(all(abs(a-b)<.01 for x,y in zip(results[0],cells) for a,b in zip(x,y)))

    def test_native_line_geometry_uses_display_rotation_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/"source.pdf"
            pdf=fitz.open();page=pdf.new_page(width=600,height=400)
            page.insert_text((30,80),"Native line with a complete ending.");page.set_rotation(90)
            pdf.save(path);pdf.close();source=path.read_bytes()
            doc=VisualDocument(path,root/"out")
            try:
                cell=doc.geometric_evidence(1)[0]["bbox"]
                crop=[cell[0],cell[1]+(cell[3]-cell[1])/3,cell[2],cell[3]-(cell[3]-cell[1])/3]
                self.assertEqual(doc.safe_bounds(1,crop),cell)
                self.assertEqual(doc.pdf[0].rotation,90)
                self.assertEqual(path.read_bytes(),source)
            finally:doc.close()

    def test_uncertainty_indices_cannot_cite_a_nonexistent_fact(self):
        for bad in ([1],[-1],[True],"0"):
            answer=source_read({"bbox":[0,0,1,1]})
            answer["uncertainties"]=[{"bbox":[0,0,1,1],"reason":"unresolved source", "fact_indices":bad}]
            with self.subTest(bad=bad),self.assertRaises(ContractError):reading(answer)

    def test_unique_quote_uses_its_source_line_instead_of_entire_paragraph_bbox(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/"lines.pdf"
            pdf=fitz.open();page=pdf.new_page();page.insert_text((30,80),"First row.\nSecond row.")
            pdf.save(path);pdf.close()
            doc=VisualDocument(path,root/"out")
            try:
                line=next(c for c in doc.geometric_evidence(1) if c.get("text")=="Second row.")
                found=doc.locate_quote(1,"Second row.",line["bbox"])
                self.assertEqual(found["source"],"pdf_native_line")
                self.assertEqual(found["bbox"],line["bbox"])
            finally:doc.close()

    def test_hybrid_raster_geometry_cannot_join_native_columns_or_leave_image_extent(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/"mixed.pdf"
            image=Image.new("L",(200,200),255);draw=ImageDraw.Draw(image)
            for x in range(20,150,12):draw.rectangle([x,40,x+7,52],fill=0)
            data=io.BytesIO();image.save(data,format="PNG")
            pdf=fitz.open();page=pdf.new_page(width=600,height=800)
            page.insert_text((30,100),"Left column source.");page.insert_text((330,100),"Right column source.")
            page.insert_image(fitz.Rect(30,300,230,500),stream=data.getvalue())
            pdf.save(path);pdf.close()
            doc=VisualDocument(path,root/"out")
            try:
                cells=doc.geometric_evidence(1)
                ink=[c["bbox"] for c in cells if c["source"]=="image_ink_geometry"]
                self.assertTrue(ink)
                self.assertTrue(all(.05<=b[0]<b[2]<=230/600 and .375<=b[1]<b[3]<=.625 for b in ink))
                guarded=doc.safe_bounds(1,[.52,.1,.95,.15])
                self.assertGreater(guarded[0],.5)
            finally:doc.close()


class CropWorkflowTests(unittest.TestCase):
    def test_declared_continuation_keeps_neighbour_source_crop_without_merging_fact_targets(self):
        class Continuation(ScriptedModel):
            def complete(self,**kwargs):
                answer,receipt=super().complete(**kwargs)
                stage,context,_=self.events[-1]
                if stage=="layout":
                    answer["regions"]=[{"id":f"r{i+1}","kind":"text","bbox":[0,i/2,1,(i+1)/2],
                                        "summary":"source paragraph","objects":[]} for i in range(2)]
                if stage=="segment":answer["assignments"][1]["continues_from"]=answer["assignments"][0]["region"]
                return answer,receipt
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/"source.pdf";make_pdf(path)
            model=Continuation();VisualPipeline(model,root/"out").run([path])
            for stage,context,labels in model.events:
                if stage!="read" or context.get("purpose"):continue
                linked=context["segmentation_hint"]["source_context_regions"]
                self.assertEqual(len(linked),1)
                self.assertEqual(len(labels),3)
                self.assertNotEqual(linked[0]["region_id"],context["target"]["id"])
                self.assertLess(context["target"]["bbox"][3]-context["target"]["bbox"][1],1)

    def test_primary_reads_round_robin_across_pages_even_at_concurrency_one(self):
        class Pages(ScriptedModel):
            def complete(self,**kwargs):
                answer,receipt=super().complete(**kwargs)
                if self.events[-1][0]=="layout":
                    answer["regions"]=[{"id":f"r{i+1}","kind":"text","bbox":[0,i/2,1,(i+1)/2],
                                        "summary":"source paragraph","objects":[]} for i in range(2)]
                return answer,receipt
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/"source.pdf";make_pdf(path,2)
            model=Pages()
            VisualPipeline(model,root/"out",settings=VisualSettings(max_calls=5,model_concurrency=1)).run([path])
            reads=[c["target"]["id"] for stage,c,_ in model.events if stage=="read"]
            self.assertEqual(reads,["p1:r1","p2:r1"])

    def test_primary_regions_are_read_before_review_or_zoom_under_tight_budget(self):
        class Frontier(ScriptedModel):
            def complete(self,**kwargs):
                answer,receipt=super().complete(**kwargs)
                stage,context,_=self.events[-1]
                if stage=="layout":
                    answer["regions"]=[{"id":f"r{i+1}","kind":"text","bbox":[0,i/3,1,(i+1)/3],
                                        "summary":"source paragraph","objects":[]} for i in range(3)]
                if stage=="read" and context["target"]["id"]=="p1:r1":
                    answer["uncertainties"]=[{"bbox":[0,0,1,1/3],"reason":"Small ambiguous source", "fact_indices":[0]}]
                return answer,receipt
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/"source.pdf";make_pdf(path)
            model=Frontier()
            result=VisualPipeline(model,root/"out",settings=VisualSettings(max_calls=5)).run([path])
            self.assertEqual([s for s,_,_ in model.events],["layout","segment","read","read","read"])
            self.assertEqual({c["target"]["id"] for s,c,_ in model.events if s=="read"},{"p1:r1","p1:r2","p1:r3"})
            self.assertEqual(result["summary"]["verified_model"],0)
            primary=result["documents"][0]["pages"][0]["regions"]
            self.assertEqual(len([r for r in primary if r.get("reading")]),3)
            self.assertEqual(result["processing_status"],"partial")

    def test_scoped_uncertainty_only_withholds_affected_fact_and_preserves_failed_reviews(self):
        class Scoped(ScriptedModel):
            indices=[0]
            failed_check=None
            def complete(self,**kwargs):
                answer,receipt=super().complete(**kwargs)
                stage,context,_=self.events[-1]
                if stage=="read":
                    second=copy.deepcopy(answer["facts"][0]);second.update(field="width",value="8.0",quote="Object W1 width 8.0 m.")
                    answer["transcript"]+="\n"+second["quote"];answer["facts"].append(second)
                    issue={"bbox":[0,0,1,1],"reason":"Unresolved part of source","kind":"crop_boundary","edges":["bottom"]}
                    if self.indices is not None:issue["fact_indices"]=self.indices
                    answer["uncertainties"]=[issue]
                if stage=="verify" and self.failed_check:
                    for verdict in answer["verdicts"]:verdict[self.failed_check]=False
                return answer,receipt
        for indices,failed,expected in (([0],None,1),(None,None,0),([],"condition_supported",0),([],"role_supported",0)):
            with self.subTest(indices=indices,failed=failed),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);path=root/"source.pdf";make_pdf(path)
                model=Scoped();model.indices=indices;model.failed_check=failed
                result=VisualPipeline(model,root/"out",settings=VisualSettings(max_rereads_per_region=0)).run([path])
                self.assertEqual(result["summary"]["verified_model"],expected)
                self.assertEqual(result["processing_status"],"partial")

    def test_crop_boundary_reread_extends_beyond_original_target(self):
        class Boundary(ScriptedModel):
            def complete(self,**kwargs):
                answer,receipt=super().complete(**kwargs)
                stage,context,_=self.events[-1]
                if stage=="layout":answer["regions"][0]["bbox"]=[.05,.05,.8,.4]
                if stage=="read" and ":zoom" not in context["target"]["id"]:
                    b=context["target"]["bbox"]
                    answer["uncertainties"]=[{"bbox":[b[0],b[3]-.01,b[2],b[3]],"reason":"Sentence cut at crop edge",
                                               "kind":"crop_boundary","edges":["bottom"],"fact_indices":[0]}]
                return answer,receipt
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/"source.pdf";make_pdf(path)
            model=Boundary();VisualPipeline(model,root/"out").run([path])
            reads=[c for s,c,_ in model.events if s=="read" and not c.get("purpose")]
            self.assertGreater(reads[1]["target"]["bbox"][3],reads[0]["target"]["bbox"][3])
            self.assertEqual(reads[1]["recovery_geometry"]["parent_bbox"],reads[0]["target"]["bbox"])


if __name__=="__main__":unittest.main()
