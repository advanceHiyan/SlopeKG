"""The archive boundary must not suppress scanned forms or trigger picture readers."""
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import AsyncMock, Mock, patch

from slopekg.parsers import classify_page
from slopekg.knowledge_document import ensure_full_page_ocr
from slopekg.visual_pipeline import VisualPipeline


class RecognitionScopeTests(TestCase):
    def test_basic_scanned_table_keeps_ocr_but_drawing_does_not(self):
        args=dict(kind='施工图',page_no=1,page_count=1,width=800,height=600)
        scan=classify_page(**args,text='',text_chars=0,image_count=1,drawing_count=0)
        drawing=classify_page(**args,text='',text_chars=0,image_count=0,drawing_count=100)
        self.assertEqual(scan['page_type'],'scanned');self.assertTrue(scan['needs_ocr'])
        self.assertEqual(drawing['page_type'],'drawing');self.assertFalse(drawing['needs_ocr'])

    def test_knowledge_does_not_recreate_drawing_ocr_jobs(self):
        parsed={'documents':[{'id':'d'}],'pages':[{'id':'p1','page':1,'page_type':'drawing','drawing_count':100},
                 {'id':'p2','page':2,'page_type':'scanned','image_count':1}], 'text_blocks':[]}
        tasks={}
        ensure_full_page_ocr(parsed,tasks,{1,2},'sha',Path('sample.pdf'),40)
        self.assertEqual([t['page'] for t in tasks.values()],[2])

    def test_visual_archives_drawing_without_reader_and_still_reads_table(self):
        with TemporaryDirectory() as tmp:
            pipeline=VisualPipeline(Mock(),Path(tmp))
            page={'page':1,'regions':[]};report={'facts':[]}
            doc=Mock(id='d',sha256='sha')
            with patch.object(pipeline,'_checkpoint'),patch.object(pipeline,'_images',return_value=[]),\
                 patch.object(pipeline,'_ask_async',new_callable=AsyncMock) as ask:
                result=asyncio.run(pipeline._read_region(doc,page,{'id':'r1','kind':'drawing','bbox':[0,0,1,1]}, {},{},report,defer_review=True))
                self.assertTrue(result['archive_only']);ask.assert_not_awaited()
                ask.return_value={'transcript':'Scanned form','tables':[],'dimensions':[],'uncertainties':[],'facts':[]}
                asyncio.run(pipeline._read_region(doc,page,{'id':'r2','kind':'table','bbox':[0,0,1,1]}, {},{},report,defer_review=True))
                self.assertEqual(ask.await_args.args[0],'read')
                self.assertEqual(len(report['archived_figures']),1)
