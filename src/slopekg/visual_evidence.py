"""Literal evidence parts and source offsets, separate from semantic assertions.

No fuzzy edit distance, numeric conversion, or inferred object/scope values.
"""
from __future__ import annotations

import re
import unicodedata


PUNCTUATION = str.maketrans({'：': ':', '，': ',', '（': '(', '）': ')', '；': ';'})
JOINERS = {';', '；', '、', ',', '，'}


def canonical(value: str) -> str:
    # Compatibility normalization of *only* Roman numeral glyphs. Global NFKC
    # destroys significant distinctions such as m² / m³ and formula powers.
    return ''.join(unicodedata.normalize('NFKC', c) if 'ROMAN NUMERAL' in unicodedata.name(c, '') else c
                   for c in unicodedata.normalize('NFC', value))


def field_parts_error(fact: dict) -> str | None:
    evidence = fact.get('field_evidence', {})
    if not isinstance(evidence, dict) or any(k not in {'value', 'qualifier', 'condition', 'time'} for k in evidence):
        return 'Invalid field_evidence keys'
    for key, item in evidence.items():
        if not isinstance(item, dict):
            return 'Field evidence must be an object'
        parts, joiner = item.get('parts'), item.get('joiner', '')
        if (not isinstance(parts, list) or not 1 <= len(parts) <= 64
                or any(not isinstance(p, str) or not p.strip() for p in parts)
                or not isinstance(joiner, str) or (len(parts) > 1 and joiner not in JOINERS)):
            return 'Invalid literal parts/joiner'
        normalize = lambda v: re.sub(r'\s+', '', canonical(v).translate(PUNCTUATION))
        if normalize(joiner.join(parts)) != normalize(fact[key]):
            return 'Evidence parts do not reconstruct the complete field'
    return None


def field_is_supported(fact: dict, key: str, fragments: list[str], literal) -> bool:
    """A declared list may have distributed evidence, never an inferred split.

    Unit strings stay indivisible. Numbers cannot be built by joining digits
    from unrelated cells. Every part uses the same strict lexical guard as a
    single literal and independent semantic review remains required.
    """
    if not fact[key]:
        return True
    if field_parts_error(fact):
        return False
    item = fact.get('field_evidence', {}).get(key)
    if item is None:
        return any(literal(fact[key], q, unit=key == 'unit') for q in fragments)
    return all(any(literal(part, q) for q in fragments) for part in item['parts'])


def align_quote(quote: str, source: str) -> dict | None:
    """Locate presentation-equivalent source text and retain original offsets.

    Only whitespace, common full-width punctuation and Roman glyphs are
    normalized. A sentence-final stop may be omitted for alignment; arbitrary
    punctuation, signs, powers and formula operators are never discarded.
    """
    def mapped(value):
        chars, positions = [], []
        for i, char in enumerate(value):
            for c in canonical(char).translate(PUNCTUATION):
                if not c.isspace():
                    chars.append(c); positions.append(i)
        return ''.join(chars), positions
    needle, _ = mapped(quote)
    haystack, offsets = mapped(source)
    for token, mode in ((needle, 'presentation'), (needle[:-1], 'terminal_stop')):
        if mode == 'terminal_stop' and (not needle.endswith(('。', '.')) or needle.endswith('...')):
            continue
        if not token:
            continue
        at = haystack.find(token)
        if at >= 0:
            start, end = offsets[at], offsets[at + len(token) - 1] + 1
            return {'start': start, 'end': end, 'text': source[start:end], 'match': mode}
    return None


EVIDENCE_PUNCTUATION = PUNCTUATION


def normalized(value: str) -> str:
    # NFKC would collapse superscript units; preserve m² versus m³.
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", value))



def source_contains(quote: str, source: str, *, table: bool = False) -> bool:
    """Align presentation, preserving numeric tokens and superscript units.

    A table's Markdown separators are presentation boundaries, never digits:
    `1 | 2` cannot certify `12`. Literal formula bars remain significant in text.
    """
    def tokens(value):
        value=canonical(value).translate(EVIDENCE_PUNCTUATION)
        if table:value=value.replace('|',' ').replace('｜',' ')
        power=r"(?:[⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+|\^[−-]?[0-9]+)?"
        word=rf"[A-Za-z]+{power}(?:/[A-Za-z]+{power})*"
        return re.findall(rf"{word}|[0-9]+(?:\.[0-9]+)?|[^\s]",value)
    def find(needle,haystack,offset=0):
        if not needle:return None
        for start in range(offset,len(haystack)-len(needle)+1):
            if haystack[start:start+len(needle)]!=needle:continue
            if needle[0][0].isdigit() and start and haystack[start-1] in {'-','−','+'}:continue
            return start
        return None
    needle,haystack=tokens(quote),tokens(source)
    if find(needle,haystack) is not None:return True
    # A regenerated sentence-final stop is presentation, not a changed value.
    # Keep every other token, including formula operators and numeric signs.
    if len(needle)>1 and needle[-1] in {"。","."} and find(needle[:-1],haystack) is not None:return True
    # An explicit omission in a quoted table row may skip cells, but cannot
    # jump to another row/object or introduce explanatory text not in source.
    if table and re.search(r"…|\.{3,}",quote):
        fragments=[tokens(q) for q in re.split(r"…+|\.{3,}",quote) if q.strip()]
        if fragments:
            for line in source.splitlines():
                row=tokens(line);offset=0
                for fragment in fragments:
                    at=find(fragment,row,offset)
                    if at is None:break
                    offset=at+len(fragment)
                else:return True
    return False



def literal_supported(value: str, quote: str, *, unit: bool = False) -> bool:
    """Literal support with token boundaries: 4.5 != 4.51, m != mm or m².

    This is a generic lexical guard, not a unit conversion or semantic judge.
    """
    value = normalized(canonical(value).translate(EVIDENCE_PUNCTUATION))
    quote = canonical(quote).translate(EVIDENCE_PUNCTUATION)
    if not value:
        return True
    word_boundary = (value[0].isascii() and value[0].isalpha()) or (value[-1].isascii() and value[-1].isalpha())
    # Keep word separators for identifiers/Roman numerals. Removing spaces
    # would make the preceding word look like an identifier prefix.
    # A scalar must match one intact numeric token. Removing whitespace from
    # `1.25\t0.75` invents `1.250.75`, falsely rejecting both real cells (and
    # `1\t2` could otherwise certify a fabricated 12). Keep scalar separators.
    scalar=bool(re.fullmatch(r"[+−-]?\d+(?:\.\d+)?",value))
    if not unit and not word_boundary and not scalar:quote=normalized(quote)
    pattern = r"\s*".join(re.escape(c) for c in value) if unit or word_boundary else re.escape(value)
    for match in re.finditer(pattern, quote):
        before = quote[match.start() - 1] if match.start() else ""
        after = quote[match.end()] if match.end() < len(quote) else ""
        if unit:
            def unit_part(char):
                return bool(char) and ((char.isascii() and char.isalpha()) or char.isdigit() or char in "/^⁻⁺")
            # A numeric value directly before a unit is expected. Prefix letters
            # and following powers/compound units change the unit's meaning.
            if (before and before.isascii() and before.isalpha()) or before in ("/", "^") or unit_part(after):
                continue
            following = quote[match.end():].lstrip()
            if following.startswith(("/", "^", "⁻", "⁺")):
                continue
        else:
            if scalar and value[0].isdigit() and re.search(r"[−-]\s*$",quote[:match.start()]):
                continue
            if value[0].isascii() and value[0].isalpha() and before and before.isascii() and before.isalnum():
                continue
            if value[-1].isascii() and value[-1].isalpha() and after and after.isascii() and after.isalnum():
                continue
            if value[0].isdigit() and before and (before.isdigit() or before in ".−-"):
                continue
            if value[-1].isdigit() and after and (after.isdigit() or (after == "." and match.end() + 1 < len(quote)
                                                                                    and quote[match.end() + 1].isdigit())):
                continue
        return True
    return False
