from copy import deepcopy
import unittest

from slopekg.risk_dataset import case_digest, label_gate, make_scenarios, quantity, validate_cases


def case():
    row = {'case_id': 'test-only-fictional-case', 'source_type': 'expert_verified_structured',
           'source_group_id': 'test-only-engineering-group', 'parent_case_id': None,
           'split': 'development_seen', 'identity': {'name': 'test-only-slope'},
           'input': {'assessment_as_of': '2026-10-01', 'condition': 'test-only-condition',
                     'material_type': '岩质', 'consequence': {'category': 'test-only-category'},
                     'observed_drainage_condition': None, 'observed_deformation_condition': None,
                     'verified_protection_condition': None, 'temporal_context': 'test-only-snapshot'},
           'targets': {'review_priority': None, 'risk_level': None},
           'provenance': {'context_evidence_ids': ['test-only-evidence']}, 'quality': {'flags': []}}
    row['input_sha256'] = case_digest(row)
    return row


def scheme(task='review_priority'):
    return {'id': 'test-only-scheme', 'version': 'test-only-version', 'task': task,
            'approval_status': 'approved', 'approved_by': 'test-only-domain-reviewer',
            'approval_evidence': 'test-only-protocol', 'levels': ['P1', 'P2', 'P3', 'P4'] if task == 'review_priority' else ['test-only-grade']}


def reviews(row, task='review_priority'):
    return [{'case_id': row['case_id'], 'task': task, 'input_sha256': row['input_sha256'],
             'scheme_id': 'test-only-scheme', 'scheme_version': 'test-only-version',
             'assessment_as_of': row['input']['assessment_as_of'], 'condition': row['input']['condition'],
             'input_verdict': 'accepted', 'field_verification_complete': True,
             'label_origin': 'human_domain_expert', 'reviewer_id': 'test-only-reviewer-' + str(i),
             'reviewer_role': 'domain_expert', 'independent_first_pass': True,
             'label': 'P1' if task == 'review_priority' else 'test-only-grade',
             'rationale': 'test-only-fixture', 'reviewed_at': '2026-10-14',
             'evidence_ids': ['test-only-evidence']} for i in range(2)]


class DatasetGateTests(unittest.TestCase):
    def test_empty_labels_are_not_training_data(self):
        row = case()
        self.assertFalse(label_gate(row, [], scheme(), 'review_priority')['eligible'])

    def test_two_matching_declared_expert_records_can_pass(self):
        row = case()
        self.assertTrue(label_gate(row, reviews(row), scheme(), 'review_priority')['eligible'])

    def test_single_review_is_insufficient(self):
        row = case()
        self.assertFalse(label_gate(row, reviews(row)[:1], scheme(), 'review_priority')['eligible'])

    def test_model_outputs_cannot_become_expert_labels(self):
        row = case(); r = reviews(row)
        r[0]['label_origin'] = 'model_prediction'
        self.assertFalse(label_gate(row, r, scheme(), 'review_priority')['eligible'])

    def test_same_reviewer_twice_is_not_independent(self):
        row = case(); r = reviews(row); r[1]['reviewer_id'] = r[0]['reviewer_id']
        self.assertFalse(label_gate(row, r, scheme(), 'review_priority')['eligible'])

    def test_disagreement_is_not_resolved_by_majority_or_highest_risk(self):
        row = case(); r = reviews(row); r[1]['label'] = 'P2'
        self.assertIn('expert_disagreement_requires_adjudication', label_gate(row, r, scheme(), 'review_priority')['blocking_reasons'])

    def test_edited_input_requires_new_reviews(self):
        row = case(); r = reviews(row); row['input']['condition'] = 'changed'
        row['input_sha256'] = case_digest(row)
        self.assertFalse(label_gate(row, r, scheme(), 'review_priority')['eligible'])

    def test_review_of_other_date_or_scheme_version_is_rejected(self):
        row = case(); r = reviews(row); r[1]['scheme_version'] = 'other'; r[0]['assessment_as_of'] = '2025-10-01'
        self.assertFalse(label_gate(row, r, scheme(), 'review_priority')['eligible'])

    def test_unapproved_risk_scale_is_blocked(self):
        row = case(); s = scheme('risk_level'); s['approval_status'] = 'draft'
        self.assertFalse(label_gate(row, reviews(row, 'risk_level'), s, 'risk_level')['eligible'])

    def test_risk_without_consequence_is_blocked(self):
        row = case(); row['input']['consequence'] = {'category': None}; row['input_sha256'] = case_digest(row)
        self.assertFalse(label_gate(row, reviews(row, 'risk_level'), scheme('risk_level'), 'risk_level')['eligible'])

    def test_missing_date_or_condition_is_not_imputed(self):
        row = case(); row['input']['assessment_as_of'] = None; row['input']['condition'] = None
        row['input_sha256'] = case_digest(row)
        self.assertFalse(label_gate(row, reviews(row), scheme(), 'review_priority')['eligible'])

    def test_unresolved_evidence_is_rejected(self):
        row = case(); r = reviews(row); r[0]['evidence_ids'] = ['invented']
        self.assertFalse(label_gate(row, r, scheme(), 'review_priority')['eligible'])

    def test_public_stability_labels_are_not_project_risk_labels(self):
        row = case(); row['source_type'] = 'public_literature_compilation'
        self.assertFalse(label_gate(row, reviews(row), scheme(), 'review_priority')['eligible'])

    def test_geometry_upper_bound_is_not_an_exact_height(self):
        q = quantity({'h_min': None, 'h_max': 28, 'h_raw': '≤28.0m'}, 'h', 'm')
        self.assertEqual(q['qualifier'], 'upper_bound')
        self.assertIsNone(q['minimum'])

    def test_reversed_nonfinite_and_boolean_geometry_rejected(self):
        for lo, hi in [(30, 20), (float('nan'), 20), (True, 20)]:
            with self.assertRaises(ValueError): quantity({'h_min': lo, 'h_max': hi}, 'h', 'm')
        with self.assertRaises(ValueError): quantity({'a_max': 100}, 'a', 'degree')

    def test_scenarios_keep_parent_and_have_no_labels_or_fs(self):
        row = case(); items = make_scenarios([row])
        self.assertEqual(len(items), 3)
        self.assertEqual(validate_cases([row] + items, {'test-only-evidence'})['status'], 'passed')
        self.assertTrue(all(x['targets'] == row['targets'] for x in items))
        self.assertTrue(all(not x['scenario']['numerical_simulation_performed'] for x in items))

    def test_synthetic_cannot_enter_independent_real_test(self):
        row = case(); items = make_scenarios([row]); items[0]['split'] = 'independent_real_test'
        self.assertEqual(validate_cases([row] + items, {'test-only-evidence'})['status'], 'failed')

    def test_undeclared_scenario_feature_change_is_rejected(self):
        row = case(); items = make_scenarios([row]); items[0]['input']['material_type'] = '土质'
        items[0]['input_sha256'] = case_digest(items[0])
        self.assertEqual(validate_cases([row] + items, {'test-only-evidence'})['status'], 'failed')

    def test_no_candidate_label_backfill(self):
        row = case(); row['targets']['risk_level'] = 'invented'
        self.assertEqual(validate_cases([row], {'test-only-evidence'})['status'], 'failed')

    def test_scenario_needs_protocol_review_before_label_admission(self):
        row = make_scenarios([case()])[0]
        self.assertFalse(label_gate(row, reviews(row), scheme(), 'review_priority')['eligible'])

    def test_identity_edit_invalidates_reviews(self):
        row = case(); r = reviews(row); row['identity']['name'] = 'different-slope'
        self.assertFalse(label_gate(row, r, scheme(), 'review_priority')['eligible'])

    def test_false_string_is_not_a_boolean_review_confirmation(self):
        row = case(); r = reviews(row); r[0]['field_verification_complete'] = 'false'
        self.assertFalse(label_gate(row, r, scheme(), 'review_priority')['eligible'])

    def test_nonindependent_or_nonexpert_reviews_blocked(self):
        row = case(); r = reviews(row); r[0]['independent_first_pass'] = False; r[1]['reviewer_role'] = 'AI'
        self.assertFalse(label_gate(row, r, scheme(), 'review_priority')['eligible'])

    def test_invalid_review_date_blocked(self):
        row = case(); r = reviews(row); r[0]['reviewed_at'] = '2026-02-30'
        self.assertFalse(label_gate(row, r, scheme(), 'review_priority')['eligible'])


if __name__ == '__main__':
    unittest.main()
