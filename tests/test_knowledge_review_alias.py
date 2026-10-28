import unittest
from slopekg.visual_contract import normalize_qualifier_verdict

class ReviewAliasTests(unittest.TestCase):
    def test_only_explicit_unambiguous_boolean_alias_is_normalized(self):
        self.assertIs(normalize_qualifier_verdict({'qualifier_supported':True})['qualifiers_supported'],True)
        self.assertIs(normalize_qualifier_verdict({'qualifier_supported':False})['qualifiers_supported'],False)
        for raw in [{'qualifier_supported':True,'qualifiers_supported':False},
                    {'qualifier_supported':False,'qualifiers_supported':True}]:
            self.assertIsNone(normalize_qualifier_verdict(raw)['qualifiers_supported'])
        self.assertIsNot(normalize_qualifier_verdict({'qualifier_supported':1}).get('qualifiers_supported'),True)
        self.assertNotIn('qualifiers_supported',normalize_qualifier_verdict({}))
