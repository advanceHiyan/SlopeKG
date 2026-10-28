"""Anonymous before/after probes; these measure mechanisms, not corpus P/R."""
from pathlib import Path
import tempfile
import unittest

from slopekg.visual_pipeline import VisualPipeline, VisualSettings
from slopekg.visual_comparison import compare_independent
from slopekg.visual_sources import fact_source_bindings
from tests.test_visual_pipeline import ScriptedModel, make_pdf, source_read


class VisualQualityRegressionTests(unittest.TestCase):
    def test_equivalent_number_format_and_wording_are_not_automatic_conflicts(self):
        fact=source_read({'bbox':[0,0,1,1]})['facts'][0]
        independent={'facts':[{**fact,'value':'4.510','scope':'Object W1','role':'reference'}]}
        comparison=compare_independent(fact,independent,[fact])
        self.assertEqual(comparison['critical_fields'],[])
        self.assertEqual(set(comparison['differences']),{'value','scope','role'})

    def test_shared_paragraph_with_multiple_objects_is_not_a_unique_pair(self):
        fact=source_read({'bbox':[0,0,1,1]})['facts'][0]
        other={**fact,'object':'W2','value':'7.83'}
        for candidates,peers in [([fact,other],[fact]),([fact],[fact,other])]:
            comparison=compare_independent(fact,{'facts':peers},candidates)
            self.assertEqual(comparison['critical_fields'],[])
            self.assertEqual(comparison['status'],'ambiguous')

    def test_identifier_and_unit_conflicts_are_not_hidden_by_a_correct_price(self):
        fact=source_read({'bbox':[0,0,1,1]})['facts'][0]
        fact.update(object='material 7812356',field='price',unit='CNY',unit_basis='kg')
        other={**fact,'object':'material 7812956','unit_basis':'t'}
        comparison=compare_independent(fact,{'facts':[other]},[fact])
        self.assertEqual(set(comparison['critical_fields']),{'object','unit_basis'})

    def test_small_budget_reaches_one_verified_page_before_later_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / 'anonymous.pdf'
            make_pdf(pdf, 5)
            model = ScriptedModel()
            result = VisualPipeline(model, root / 'out', settings=VisualSettings(
                window_pages=1, max_calls=6)).run([pdf])
            self.assertEqual(result['summary']['calls'], 6)
            self.assertEqual(result['summary']['verified_model'], 1)
            self.assertEqual(result['documents'][0]['pages'][0]['status'], 'completed')
            self.assertEqual(len(result['documents'][0]['pages']), 5)
            self.assertEqual(result['processing_status'], 'partial')

    def test_default_window_finishes_four_pages_with_a_bounded_budget(self):
        import fitz
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pdf=root/'anonymous.pdf'
            document=fitz.open()
            for _ in range(12):
                document.new_page(width=300,height=500).insert_text((30,40),'Object W1 height 4.51 m.')
            document.save(pdf);document.close()
            model=ScriptedModel()
            result=VisualPipeline(model,root/'out',settings=VisualSettings(max_calls=22)).run([pdf])
            self.assertEqual(result['summary']['calls'],22)
            self.assertEqual(result['summary']['verified_model'],4)
            self.assertTrue(all(p['status']=='completed' for p in result['documents'][0]['pages'][:4]))
            stages=[(s,c.get('page')) for s,c,_ in model.events]
            through_audit=stages[:stages.index(('audit',4))+1]
            self.assertFalse(any(stage=='layout' and page>=6 for stage,page in through_audit))
            self.assertEqual(len(result['documents'][0]['pages']),12)

    def test_equal_numbers_in_other_cells_do_not_invent_source_links(self):
        answer=source_read({'bbox':[0,0,1,1]})
        cell={'row':3,'column':2,'row_span':1,'column_span':1,'header':'height (m)','text':'4.51'}
        answer['tables']=[{'caption':'height table','bbox':[0,0,1,1],'cells':[
            {**cell,'disposition':'fact','fact_indices':[0]},
            {**cell,'row':8,'disposition':'unresolved','fact_indices':[]}]}]
        bindings=fact_source_bindings(answer)[0]
        self.assertEqual(len(bindings),1)
        self.assertEqual(bindings[0]['row'],3)

    def test_cross_window_source_links_and_section_identity_survive_streaming(self):
        class ContinuingRead(ScriptedModel):
            def complete(self,**kwargs):
                answer,receipt=super().complete(**kwargs)
                stage,context,_=self.events[-1]
                if stage=='segment' and context['previous_boundary']:
                    answer['assignments'][0]['continues_from']=context['previous_boundary'][-1]['region']
                return answer,receipt
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pdf=root/'anonymous.pdf';make_pdf(pdf,3)
            model=ContinuingRead()
            pipeline=VisualPipeline(model,root/'out',settings=VisualSettings(window_pages=1))
            result=pipeline.run([pdf])
            self.assertEqual(result['processing_status'],'completed')
            self.assertEqual(result['summary']['verified_model'],1)
            evidence=result['documents'][0]['facts'][0]['evidence']
            self.assertEqual({e['page'] for e in evidence},{1,2,3})
            reads=[c for stage,c,_ in model.events if stage=='read' and not c.get('purpose')]
            self.assertEqual({c['segmentation_hint']['section_scope'] for c in reads},{'p1:r1'})
            self.assertEqual(reads[1]['segmentation_hint']['source_context_regions'][0]['page'],1)
            calls=len(model.events)
            self.assertEqual(pipeline.run([pdf])['summary']['calls'],0)
            self.assertEqual(len(model.events),calls)

    def test_blind_field_conflict_is_not_hidden_by_identical_transcripts(self):
        class ConflictingRead(ScriptedModel):
            def complete(self, **kwargs):
                answer, receipt = super().complete(**kwargs)
                stage, context, _ = self.events[-1]
                if stage == 'read':
                    quote = 'Object W1 height 4.51 m; reference limit 7.83 m.'
                    answer['transcript'] = quote
                    answer['facts'][0]['quote'] = quote
                    if context.get('purpose'):
                        answer['facts'][0]['value'] = '7.83'
                return answer, receipt
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / 'anonymous.pdf'
            make_pdf(pdf)
            model = ConflictingRead()
            result = VisualPipeline(model, root / 'out', settings=VisualSettings(
                max_rereads_per_region=0)).run([pdf])
            self.assertEqual(result['summary']['verified_model'], 0)
            fact = result['documents'][0]['facts'][0]
            self.assertEqual(fact['independent_comparison']['critical_fields'], ['value'])

    def test_table_coordinates_and_headers_reach_the_reviewer(self):
        class TableRead(ScriptedModel):
            def complete(self, **kwargs):
                answer, receipt = super().complete(**kwargs)
                stage, _, _ = self.events[-1]
                if stage == 'read':
                    answer['tables'] = [{'caption': 'Measured heights', 'bbox': [0, 0, 1, 1],
                        'cells': [{'row': 3, 'column': 2, 'row_span': 1, 'column_span': 1,
                            'header': 'height (m)', 'text': '4.51',
                            'disposition': 'fact', 'fact_indices': [0]}]}]
                return answer, receipt
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / 'anonymous.pdf'
            make_pdf(pdf)
            model = TableRead()
            result = VisualPipeline(model, root / 'out').run([pdf])
            self.assertEqual(result['summary']['verified_model'], 1)
            context = next(c for s, c, _ in model.events if s == 'verify')
            binding = context['candidates'][0]['source_bindings'][0]
            self.assertEqual((binding['table_index'], binding['row'], binding['column']), (0, 3, 2))
            self.assertEqual(binding['header'], 'height (m)')
            self.assertEqual(binding['caption'], 'Measured heights')
            fact = result['documents'][0]['facts'][0]
            self.assertEqual(fact['source_bindings'][0]['text'], '4.51')

    def test_resolving_a_blind_conflict_still_requires_all_original_checks(self):
        class ResolvingRead(ScriptedModel):
            reject_unit=False
            def complete(self,**kwargs):
                answer,receipt=super().complete(**kwargs)
                stage,context,_=self.events[-1]
                if stage=='read':
                    quote='Object W1 height 4.51 m; reference limit 7.83 m.'
                    answer['transcript']=quote
                    answer['facts'][0]['quote']=quote
                    if context.get('purpose'):answer['facts'][0]['value']='7.83'
                if stage=='verify':
                    for verdict in answer['verdicts']:
                        verdict['difference_resolutions']={'value':{'decision':'candidate_supported',
                            'reason':'Source labels 4.51 as the measured height and 7.83 as a reference limit.'}}
                        verdict['unit_supported']=not self.reject_unit
                return answer,receipt
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pdf=root/'anonymous.pdf';make_pdf(pdf)
            for reject in (False,True):
                model=ResolvingRead();model.reject_unit=reject
                result=VisualPipeline(model,root/str(reject),settings=VisualSettings(
                    max_rereads_per_region=0)).run([pdf])
                self.assertEqual(result['summary']['verified_model'],0 if reject else 1)
                fact=result['documents'][0]['facts'][0]
                self.assertEqual(fact['value'],'4.51')
                self.assertEqual(fact['independent_comparison']['differences']['value']['independent'],'7.83')

    def test_declared_but_unrelated_cell_cannot_approve_a_fact(self):
        class WrongCell(ScriptedModel):
            def complete(self,**kwargs):
                answer,receipt=super().complete(**kwargs)
                if self.events[-1][0]=='read':
                    answer['tables']=[{'caption':'arbitrary table','bbox':[0,0,1,1],
                        'cells':[{'row':8,'column':2,'row_span':1,'column_span':1,
                            'header':'height','text':'73.6','disposition':'fact','fact_indices':[0]}]}]
                return answer,receipt
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pdf=root/'anonymous.pdf';make_pdf(pdf)
            result=VisualPipeline(WrongCell(),root/'out',settings=VisualSettings(
                max_rereads_per_region=0,audit_rounds=1)).run([pdf])
            self.assertEqual(result['summary']['verified_model'],0)
            self.assertIn('source_binding_not_grounded',result['documents'][0]['facts'][0]['quality_issues'])


if __name__ == '__main__':
    unittest.main()
