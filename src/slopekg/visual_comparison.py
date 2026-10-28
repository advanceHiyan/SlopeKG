"""Compare independent assertions without fuzzy entity/value equivalence.

Different wording alone is diagnostic, never a veto. A unique shared source
quote can expose conflicting numbers, identifiers or units; resolving these
requires an explicit image-review decision, not a majority vote.
"""
import re
from decimal import Decimal

from .visual_contract import FACT_KEYS
from .visual_evidence import canonical, PUNCTUATION
from .visual_sources import identifier_atoms


def _key(value):
    return re.sub(r'\s+', '', canonical(value).translate(PUNCTUATION)).rstrip('。')


def compare_independent(fact, independent, candidates=()):
    if independent is None:
        return {'status': 'disabled', 'critical_fields': [], 'differences': {}}
    peers = [p for p in independent['facts']
             if _key(p['field']) == _key(fact['field']) and _key(p['quote']) == _key(fact['quote'])]
    primary = [p for p in candidates
               if _key(p['field']) == _key(fact['field']) and _key(p['quote']) == _key(fact['quote'])]
    if len(peers) != 1 or len(primary) > 1:
        return {'status': 'unmatched' if not peers else 'ambiguous',
                'peer_count': len(peers), 'critical_fields': [], 'differences': {}}
    peer = peers[0]
    keys = (*FACT_KEYS, 'unit_basis')
    differences = {k: {'candidate': fact.get(k, ''), 'independent': peer.get(k, '')}
                   for k in keys if _key(fact.get(k, '')) != _key(peer.get(k, ''))}
    critical = []
    scalar = r'[+−-]?\d+(?:\.\d+)?'
    for key in differences:
        a, b = fact.get(key, ''), peer.get(key, '')
        if ((key == 'value' and re.fullmatch(scalar, a.strip()) and re.fullmatch(scalar, b.strip())
             and Decimal(a.strip().replace('−','-')) != Decimal(b.strip().replace('−','-')))
                or (key in {'unit', 'unit_basis'} and a and b)
                or (key in {'object', 'scope'} and identifier_atoms(a) and identifier_atoms(b)
                    and identifier_atoms(a) != identifier_atoms(b))):
            critical.append(key)
    return {'status': 'different' if differences else 'agreed', 'peer_count': 1,
            'critical_fields': critical, 'differences': differences}


def unresolved_differences(comparison, review):
    resolutions = review.get('difference_resolutions', {})
    if not isinstance(resolutions, dict):
        resolutions = {}
    def resolved(field):
        item = resolutions.get(field)
        return (isinstance(item, dict) and item.get('decision') == 'candidate_supported'
                and isinstance(item.get('reason'), str) and bool(item['reason'].strip()))
    return [field for field in comparison['critical_fields'] if not resolved(field)]
