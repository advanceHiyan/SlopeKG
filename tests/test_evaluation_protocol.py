import unittest
from slopekg.evaluation_protocol import validate_document_partition


class EvaluationPartitionTests(unittest.TestCase):
    def test_renaming_duplicate_pdf_cannot_create_an_independent_holdout(self):
        manifest={'exposed_source_sha256':['a'*64], 'documents':[
            {'id':'original.pdf','source_sha256':'a'*64,'split':'development'},
            {'id':'renamed.pdf','source_sha256':'a'*64,'split':'holdout'}]}
        result=validate_document_partition(manifest)
        self.assertFalse(result['valid_independent_holdout'])
        self.assertEqual({e['reason'] for e in result['errors']},
                         {'previously_exposed_document','same_document_in_multiple_splits'})

    def test_no_holdout_is_not_passed_and_unseen_content_is_accepted(self):
        manifest={'documents':[{'id':'old','source_sha256':'a'*64,'split':'development'}]}
        self.assertFalse(validate_document_partition(manifest)['valid_independent_holdout'])
        manifest['documents'].append({'id':'new','source_sha256':'b'*64,'split':'holdout'})
        self.assertTrue(validate_document_partition(manifest)['valid_independent_holdout'])
