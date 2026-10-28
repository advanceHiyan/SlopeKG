from pathlib import Path
import copy
import hashlib
import tempfile
import unittest
from unittest.mock import patch

import fitz
from slopekg.rule_extraction import RuleExtractionPipeline, select_rule_pages, validate_page_selection, extract_rule_page
from slopekg.provenance import runtime_identity, source_revision, version_status
from slopekg.pipeline import DemoPipeline
from slopekg.storage import write_json, read_json
from slopekg.extractors import merge_slope_candidates
from tests.support import workspace_paths


class IndependentClosureTests(unittest.TestCase):
    def test_page_grammar_preserves_auto_and_explicit_intent(self):
        self.assertEqual(select_rule_pages('auto', 5, [3,1,3]), [1,3])
        self.assertEqual(select_rule_pages('auto', 3, []), [])
        self.assertEqual(select_rule_pages('auto', 3, [], include_unselected=True), [1,2,3])
        self.assertEqual(select_rule_pages('none', 3, [1], include_unselected=True), [])
        self.assertEqual(select_rule_pages('1-2, 4，2', 5, []), [1,2,4])
        for invalid in ['0','4','2-1','1-99999999','','1.2','all,2',True,None]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                select_rule_pages(invalid, 3, [])
        with self.assertRaises(ValueError):validate_page_selection([{'id':'a','pages':3}], {'unknown':'all'})

    def pdf(self, path):
        doc=fitz.open()
        for n in range(2):
            page=doc.new_page()
            page.insert_textbox(fitz.Rect(20,20,550,450), ('Reference material and observations are pending professional review.\n'*12),fontsize=10)
        doc.save(path);doc.close()

    def test_knowledge_fallback_reads_content_but_never_approves_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            paths=workspace_paths(Path(directory));paths.raw_dir.mkdir(parents=True)
            pdf=paths.raw_dir/'anonymous.pdf';self.pdf(pdf)
            doc=dict(id='a',path=str(pdf.relative_to(paths.root)),pages=2,source_type='taxonomy_reference',
                     priority='supporting',code='anonymous',source_sha256='v1',target_pages=[],authority='reference')
            pipeline=RuleExtractionPipeline(paths)
            with patch.object(pipeline,'catalog_documents',return_value=[copy.deepcopy(doc)]),patch('slopekg.rule_extraction.llm_status',return_value={}):
                pipeline.run(use_llm=False,include_unselected=True)
            library=read_json(paths.rules_json);result=library['documents'][0]
            self.assertEqual(result['selected_pages'],[1,2]);self.assertEqual(result['read_pages'],[1,2])
            self.assertEqual(result['content_status'],'read_no_candidates')
            self.assertFalse(library['execution_enabled']);self.assertEqual(library['stats']['approved_rules'],0)
            before=paths.rules_json.read_bytes()
            with patch.object(pipeline,'catalog_documents',return_value=[copy.deepcopy(doc)]),self.assertRaises(ValueError):
                pipeline.run(use_llm=False,page_selection={'a':'3'})
            self.assertEqual(paths.rules_json.read_bytes(),before)

    def test_changed_source_refreshes_stable_evidence_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pdf=root/'source.pdf';self.pdf(pdf);images=root/'pages';images.mkdir()
            doc=dict(id='a',code='ref',path='source.pdf',source_sha256='v1')
            row,_=extract_rule_page(pdf,doc,1,images,root=root,ocr_engine=None)
            image=root/row['image_path'];before=hashlib.sha256(image.read_bytes()).hexdigest()
            source=fitz.open(pdf);source[0].draw_rect(fitz.Rect(20,500,200,600),fill=(1,0,0));source.saveIncr();source.close()
            extract_rule_page(pdf,{**doc,'source_sha256':'v2'},1,images,root=root,ocr_engine=None)
            self.assertNotEqual(hashlib.sha256(image.read_bytes()).hexdigest(),before)

    def test_version_status_does_not_certify_legacy_or_different_artifacts(self):
        identity=runtime_identity()
        self.assertEqual(version_status(identity),'current')
        self.assertEqual(version_status({}),'unversioned')
        self.assertEqual(version_status({'code_revision':'other'}),'different_implementation')
        identity['code_revision']='tampered';self.assertNotEqual(runtime_identity()['code_revision'],'tampered')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);p=root/'example.py';p.write_text('value=1\n')
            first=source_revision(root);p.write_bytes(b'value=1\r\n');self.assertEqual(source_revision(root),first)
            p.write_text('value=2\n');self.assertNotEqual(source_revision(root),first)
            paths=workspace_paths(root);write_json(paths.state_json,{'updated_at':'historical'})
            self.assertEqual(DemoPipeline(paths).status()['version_status'],'unversioned')
            write_json(paths.basic_graph_json, {'nodes':[]})
            write_json(paths.basic_state_json, {'producer':runtime_identity()})
            write_json(paths.web_data_dir / 'deep_graph.json', {'nodes':[]})
            self.assertEqual(DemoPipeline(paths).status()['layer_versions'], {'basic':'current','deep':'removed'})

    def test_competing_values_keep_both_sources_without_rewriting_selected_value(self):
        a=dict(route_code='G777',station='K1+000-K1+100',method='inventory',page=1,confidence=.9,
               evidence_block_id='a',document_id='first',slope_height_max_m=15,source_alias='first wording')
        b={**a,'method':'section','page':2,'evidence_block_id':'b','document_id':'second','slope_height_max_m':27,'source_alias':'other wording'}
        original=copy.deepcopy([a,b]);row=merge_slope_candidates([a],[b])[0]
        self.assertEqual(row['slope_height_max_m'],15)
        issue=row['source_disagreements'][0]
        self.assertEqual(issue['field'],'slope_height_max_m')
        self.assertEqual([v['value'] for v in issue['alternatives']],[15,27])
        self.assertEqual([v['sources'][0]['document_id'] for v in issue['alternatives']],['first','second'])
        self.assertEqual(issue['review_status'],'needs_scope_and_source_review')
        self.assertEqual([a,b],original)
        self.assertEqual(merge_slope_candidates([a],[{**b,'slope_height_max_m':15.0}])[0]['source_disagreements'],[])

    def test_section_candidates_survive_first_value_merge(self):
        row=dict(route_code='G888',station='K7+000-K7+100',method='section',page=1,confidence=.9,evidence_block_id='a',
                 slope_aspect_deg=0,_section_value_candidates=[dict(field='slope_aspect_deg',value=90,evidence={'page':2,'document_id':'d'})])
        result=merge_slope_candidates([row])[0]
        self.assertEqual([a['value'] for a in result['source_disagreements'][0]['alternatives']],[0,90])


if __name__=='__main__':unittest.main()
