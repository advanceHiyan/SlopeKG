"""Validate document-level evaluation independence before reporting a score."""
from __future__ import annotations

import re
from typing import Any


def validate_document_partition(manifest: dict[str, Any]) -> dict[str, Any]:
    """Reject reused source content even when its filename or slope ID changes.

    The exposure list is an explicit, maintained provenance declaration. This
    check detects declared leakage; it cannot discover unrecorded exposure.
    """
    errors = []
    groups: dict[str, set[str]] = {}
    exposed = set(manifest.get('exposed_source_sha256', []))
    for row in manifest.get('documents', []):
        digest, split = row.get('source_sha256', ''), row.get('split')
        if not re.fullmatch(r'[0-9a-f]{64}', digest):
            errors.append({'document': row.get('id'), 'reason': 'invalid_source_sha256'})
        if split not in {'development', 'holdout'}:
            errors.append({'document': row.get('id'), 'reason': 'invalid_split'})
        groups.setdefault(digest, set()).add(split)
        if split == 'holdout' and digest in exposed:
            errors.append({'document': row.get('id'), 'reason': 'previously_exposed_document'})
    for digest, splits in groups.items():
        if len(splits) > 1:
            errors.append({'source_sha256': digest, 'reason': 'same_document_in_multiple_splits'})
    holdout = {digest for digest, splits in groups.items() if 'holdout' in splits}
    if not holdout:
        errors.append({'reason': 'no_unseen_document_holdout'})
    return {'valid_independent_holdout': not errors, 'errors': errors,
            'unique_source_documents': len(groups), 'holdout_documents': len(holdout),
            'limitation': 'validates_declared_exposure_only_not_statistical_representativeness'}
