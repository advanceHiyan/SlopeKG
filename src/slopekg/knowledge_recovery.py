"""Bounded source coverage recovery, independent of subject/industry names."""
import re
from .knowledge_document import wire_source


def recovery_package(doc,settings,*,context=None):
    """A support heading must not make its unassigned body disappear.

    This selects original unassigned source, not expected fields or answers.
    It never manufactures a fact from a heading or a bare measurement.
    """
    units={u['id']:u for u in doc['units']}
    heading_refs=[]
    for ref in doc.get('references',[]):
        for mention in ref.get('mentions',[]):
            unit=units.get(mention['span_id'],{})
            text=unit.get('text','').strip()
            if unit.get('source')=='pdf_table':continue
            if re.fullmatch(r'(?:\d+(?:\.\d+)*[、.]?\s+|#{1,6}\s+)[^\n]{2,180}',text):
                heading_refs.append(mention['span_id'])
    if not heading_refs:return None
    attributed={key for e in doc['entities'] for key in e.get('unit_ids',[])}
    missing=[u for u in doc['units'] if u['id'] not in attributed and not u.get('is_header')
        and len(re.findall(r'[^\W\d_]',u['text']))>=2]
    if not any(u['id'] not in heading_refs for u in missing):return None
    selected=[];size=0;remaining=[]
    for unit in missing:
        cost=len(unit['text'])*2+160
        if size+cost<=settings.batch_chars:
            selected.append(unit['id']);size+=cost
        else:remaining.append(unit['id'])
    anchors=[m['span_id'] for e in [*doc['entities'],*doc.get('references',[])]
             for m in e.get('mentions',[])[:1]]
    from .knowledge_batching import source_closure, prompt_chars
    from .knowledge_prompts import RECOVER
    while True:
        keys=list(dict.fromkeys([*selected,*heading_refs,*anchors]))
        chosen=set(source_closure([key for key in keys if key in units],units))
        package={'source_spans':[wire_source(u) for u in doc['units'] if u['id'] in chosen],
                 'unassigned_span_ids':list(selected)}
        if context is None or prompt_chars(RECOVER,{**context,**package})<=settings.max_prompt_chars or not selected:
            return {**package,'remaining_span_ids':[u['id'] for u in missing if u['id'] not in selected]}
        selected.pop()
