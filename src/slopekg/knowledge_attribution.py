"""Source-local safeguards for low-information attribution claims."""
from decimal import Decimal, InvalidOperation
import re
from .visual_evidence import literal_supported


def numeric_fragment(text):
    return bool(re.search(r'\d',text) and re.fullmatch(r'[\d\s.,+−\-/%°^²³×*∠:;()（）~～]+(?:[A-Za-zμ]+(?:\^?[²³23])?(?:/[A-Za-zμ]+(?:\^?[²³23])?)?)?',text.strip()))


def source_pages(entity,units):
    pages={m['page'] for m in entity.get('mentions',[]) if m.get('page') is not None}
    labels=[entity.get('name',''),*entity.get('aliases',[])]
    pages.update(u['page'] for u in units.values() if any(label.strip() and literal_supported(label,u.get('text','')) for label in labels))
    return sorted(pages)


def source_local(entity,unit,units):
    """Bare measurements cannot establish a new cross-page object identity.

    A continued record is allowed when this page has an actual named mention.
    Rejection preserves an unresolved source, never assigns a preferred owner.
    """
    if not numeric_fragment(unit['text']):return True
    pages=entity.get('source_anchor_pages')
    return unit['page'] in (pages if pages is not None else source_pages(entity,units))


def _numbers(text):
    values={}
    for match in re.finditer(r'(?<![A-Za-z0-9.])[+−-]?\d+(?:\.\d+)?(?![A-Za-z0-9.])',str(text)):
        token=match[0]
        try:key=str(Decimal(token.replace('−','-')).normalize())
        except InvalidOperation:continue
        values.setdefault(key,token)
    return values


def numeric_residuals(doc):
    """Keep unrepresented source numbers visible without inventing facts.

    Identifiers/reference values can also occur here. This is a residual source
    checklist, never a factual-recall measurement or an automatic repair queue.
    """
    used={}
    for item in doc.get('facts',[]):
        if item.get('status')=='rejected':continue
        tokens=_numbers(' '.join(str(item.get(k,'')) for k in ('value','unit','qualifier','condition','time')))
        for e in item.get('evidence',[]):used.setdefault(e['span_id'],set()).update(tokens)
    residuals=[]
    for unit in doc.get('units',[]):
        if unit.get('is_header'):continue
        missing=[token for key,token in _numbers(unit.get('text','')).items() if key not in used.get(unit['id'],set())]
        if missing:residuals.append({'span_id':unit['id'],'page':unit['page'],'tokens':missing,
            'owner_ids':[e['id'] for e in doc['entities'] if unit['id'] in e.get('unit_ids',[])],
            'status':'unclassified_source_values'})
    return residuals
