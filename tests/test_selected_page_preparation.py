from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
import fitz
from slopekg.parsers import PdfParser
from slopekg.knowledge_document import prepare_document
from slopekg.knowledge_settings import KnowledgeSettings

class SelectedPagePreparation(unittest.TestCase):
    def test_selected_pages_keep_original_ids_and_equal_full_parse_content(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'sample.pdf';pdf=fitz.open()
            for n in range(3):pdf.new_page().insert_text((50,60),'Machine '+str(n+1)+' pressure 12 MPa. '*8)
            pdf.save(source);pdf.close()
            full=PdfParser(root).parse(source,generic=True)
            with patch('slopekg.parsers.extract_tables',return_value=[]) as tables:
                selected=PdfParser(root).parse(source,generic=True,selected_pages=[1,3])
            self.assertEqual([c.args[1] for c in tables.call_args_list],[0,2])
            self.assertEqual(selected.text_blocks,[b for b in full.text_blocks if b['page'] in {1,3}])
            self.assertEqual(selected.documents[0]['parse_scope'],'pages:1,3')
            self.assertEqual([(p['page'],p['text_sampled']) for p in selected.pages],[(1,True),(2,False),(3,True)])
            with patch('slopekg.parsers.extract_tables',return_value=[]):
                prepared=prepare_document(source,KnowledgeSettings(),root=root,selected_pages=[3])
            self.assertEqual({u['page'] for u in prepared['units']},{3})
            self.assertEqual(prepared['source_page_count'],3)
            for invalid in [[],[0],[4],[True]]:
                with self.assertRaises(ValueError):PdfParser(root).parse(source,selected_pages=invalid)

if __name__=='__main__':unittest.main()
