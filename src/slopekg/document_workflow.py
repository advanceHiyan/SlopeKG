"""Compact, source-preserving requests and bounded structural repairs."""
from copy import deepcopy
from .visual_evidence import literal_supported
from .document_contract import normalized


def wire_claim(c):
    fields=('id','subject','parent_subject','scope','kind','field','value','unit','unit_basis',
            'time','condition','role','target','group_ids','quality_issues')
    out={k:c[k] for k in fields if k in c}
    for key in ('evidence','parent_evidence'):
        out[key]=[{'block_id':e['block_id'],'quote':e['quote']} for e in c.get(key,[])]
    return out


def repair_source_links(pages):
    """Only repair uniquely matched text at the same physical location.

    No new text, semantic ownership or native-verification promotion. Full old
    blocks remain in the repair receipt; ambiguous matches remain unresolved.
    """
    changes=[]
    for p in pages:
        blocks=p['blocks'];removed=set()
        for raw in blocks:
            if raw['source_type']!='unreconstructed_native':continue
            x=raw['bbox'];area=max(0,(x[2]-x[0])*(x[3]-x[1]))
            matches=[]
            for b in blocks:
                if b['source_type']=='unreconstructed_native' or b['kind']=='footer':continue
                y=b['bbox'];overlap=max(0,min(x[2],y[2])-max(x[0],y[0]))*max(0,min(x[3],y[3])-max(x[1],y[1]))
                if area and overlap/area>=.9 and normalized(raw['text']) in normalized(b['text']) and literal_supported(raw['text'],b['text']):matches.append(b)
            if len(matches)!=1:continue
            b=matches[0]
            changes.append({'action':'restore_source_link','page':p['page'],'original_block':deepcopy(raw),
                'target_block_id':b['id'],'previous_source_ids':list(b['source_ids'])})
            b['source_ids']=list(dict.fromkeys(b['source_ids']+raw['source_ids']))
            removed.add(raw['id'])
        p['blocks']=[b for b in blocks if b['id'] not in removed]
        if not any(b['source_type']=='unreconstructed_native' for b in p['blocks']):
            p['issues']=[i for i in p.get('issues',[]) if i!='native_sources_omitted']
        p['status']='partial' if p.get('issues') or p.get('unread_regions') or any(b.get('issues') for b in p['blocks']) else 'reconstructed'
    return changes


def apply_review(claims,review):
    by_id={r['claim_id']:r for r in review['reviews']}
    for c in claims:
        r=by_id[c['id']];c['model_review']=r
        revised=r.get('rechecked_claim') or r.get('rechecked_evidence')
        if revised:
            c['before_evidence_review']={k:deepcopy(c.get(k)) for k in revised}
            c.update(revised)
        if r['verdict']=='contradicted':c.update(status='rejected',reason=r['reason'])
        elif r['verdict']=='supported' and not c['quality_issues']:
            native=all(e['source_type']=='native_reordered' for e in c['evidence']+c.get('parent_evidence',[]))
            c.update(status='source_aligned' if native else 'model_supported',reason='same_model_source_review_not_independent_verification')
        else:c.update(status='pending',reason=r['reason'])
    for c in review['additions']:c.update(origin_stage='document_reconcile',reason='added_during_review_requires_followup')


def audit_index(claims,groups,links):
    fields=('id','subject','parent_subject','scope','field','value','unit','role','condition','time','target','group_ids')
    return {'groups':[{'id':g['id'],'name':g['name'],'kind':g['kind']} for g in groups],
        'links':links,'claim_index':[{**{k:c[k] for k in fields if c.get(k) not in (None,'',[])},
            'source_blocks':list(dict.fromkeys(e['block_id'] for e in c['evidence']))} for c in claims]}


def validate_audit(answer,claims):
    from .document_contract import object_rows
    from .visual_contract import ContractError
    known={c['id'] for c in claims}
    rows=object_rows(answer,'flags')
    for r in rows:
        if not isinstance(r.get('claim_ids'),list) or not r['claim_ids'] or any(i not in known for i in r['claim_ids']) or not isinstance(r.get('reason'),str):
            raise ContractError('Invalid cross-group audit flag')
    return {'flags':rows,'issues':answer.get('issues',[])}
