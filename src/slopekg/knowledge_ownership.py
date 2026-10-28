"""Explicit document heading scopes, independent of industry or identifiers."""
import re
import unicodedata
from .visual_evidence import literal_supported

def canonical(value):
    return ''.join(unicodedata.normalize('NFC',value).casefold().split())

def explicit_section_owners(doc):
    entities=[e for e in doc['entities'] if e['kind']=='entity']
    stack=[];result={}
    for unit in doc['units']:
        headings=[]
        for line in unit['text'].splitlines():
            m=re.fullmatch(r'\s*(\d+(?:\.\d+)*)(?:[、.]?)[ \t]+([^\n]{2,180})\s*',line)
            if m:headings.append((tuple(int(n) for n in m[1].split('.')),m[2]))
        if headings:
            rank,title=headings[-1];label=canonical(title);candidates=[]
            for e in entities:
                size=max((len(canonical(n)) for n in [e['name'],*e['aliases']] if canonical(n)
                    and literal_supported(n.casefold(),title.casefold())),default=0)
                if size:candidates.append((size,e['id']))
            best=max((size for size,_ in candidates),default=0)
            winners={key for size,key in candidates if size==best}
            owner=next(iter(winners)) if len(rank)>1 and len(winners)==1 else None
            descriptive=bool(re.search(r'[\u4e00-\u9fff]{2}|[A-Za-z]{4}',title))
            boundary=(not candidates and descriptive) or (len(rank)>1 and len(winners)>1)
            if owner or boundary:
                while stack and (len(stack[-1]['rank'])>=len(rank) or stack[-1]['rank'][:len(rank)-1]!=rank[:-1]):
                    old=stack.pop();old['end_span_id']=unit['id']
                stack.append({'rank':rank,'owner_id':owner,'header_span_id':unit['id'],'end_span_id':None})
        active=next((r for r in reversed(stack) if r['owner_id']),None)
        result[unit['id']]=active if len(headings)<=1 else None
    return result

def owner_proposals(doc,literal_supported):
    scopes=explicit_section_owners(doc);known={e['id']:e for e in doc['entities']};proposals=[]
    for fact in sorted(doc['facts'],key=lambda f:f['id']):
        topic=known.get(fact['entity_id'])
        if not topic or topic['kind']!='concept' or fact['status']=='rejected' or fact['quality_issues']:continue
        evidence=[e for e in fact['evidence'] if literal_supported(fact['value'],e['quote'])]
        contexts=[scopes.get(e['span_id']) for e in evidence]
        if not contexts or any(c is None for c in contexts):continue
        owners={c['owner_id'] for c in contexts}
        if len(owners)!=1:continue
        c=contexts[0]
        proposals.append({'original':fact,'owner_id':next(iter(owners)),'topic_id':topic['id'],
            'header_span_id':c['header_span_id'],'end_span_id':c['end_span_id']})
    return proposals
