"""Loss-aware reconstruction, overlapping groups and source-bound candidate checks."""
from collections import defaultdict
import re
from .model_client import content_hash
from .visual_contract import ContractError, bbox
from .knowledge_evidence import unit_supported
from .visual_evidence import literal_supported


def normalized(value):
    return re.sub(r'\s+', '', str(value)).replace('｜','|')


def object_rows(answer,key):
    rows=answer.get(key)
    if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):
        raise ContractError('Expected object list: '+key)
    return rows


def reconstruct(answer, pages):
    rows=object_rows(answer,'pages')
    expected={p['page']:p for p in pages}
    if sorted(r.get('page',-1) for r in rows)!=sorted(expected):
        raise ContractError('Reconstruction must cover each supplied page exactly once')
    result=[]
    for row in sorted(rows,key=lambda r:r['page']):
        number=row['page']; atoms={a['id']:a for a in expected[number]['native_sources']}
        blocks=[]; seen=set(); local_ids=set(); issues=[]
        for raw in object_rows(row,'blocks'):
            if not isinstance(raw.get('id'),str) or raw['id'] in local_ids:raise ContractError('Duplicate/invalid block ID')
            local_ids.add(raw['id'])
            ids=raw.get('source_ids')
            if not isinstance(ids,list) or any(not isinstance(i,str) or i not in atoms for i in ids):
                raise ContractError('Block cites unprovided native source')
            if len(set(ids))!=len(ids):raise ContractError('Duplicate source inside block')
            if seen.intersection(ids):issues.append('native_source_used_in_multiple_blocks')
            seen.update(ids)
            value=raw.get('text')
            if not isinstance(value,str) or not value.strip():raise ContractError('Empty reconstructed block')
            if raw.get('kind') not in {'heading','paragraph','table','figure','footer'}:raise ContractError('Invalid block kind')
            native='\n'.join(atoms[i]['text'].strip() for i in ids)
            # Table separators are presentation; all supplied source text remains inspectable.
            clean=lambda s:re.sub(r'[\s|]', '', s)
            matched=bool(ids) and clean(native)==clean(value)
            source_type='native_reordered' if matched else 'visual_transcription'
            blocks.append({**raw,'id':f'p{number}_{raw["id"]}','page':number,'bbox':bbox(raw.get('bbox')),
                'source_ids':ids,'text':value,'native_text':native,'source_type':source_type,
                'transcription_status':'native_text_preserved' if matched else 'requires_image_verification',
                'issues':list(raw.get('issues',[]))})
        excluded=object_rows(row,'excluded_sources')
        for entry in excluded:
            if entry.get('id') not in atoms or not isinstance(entry.get('reason'),str) or not entry['reason'].strip():
                raise ContractError('Invalid excluded-source account')
            # Exclusion is a model proposal, not permission to erase source text.
            atom=atoms[entry['id']]
            entry.update(text=atom['text'],bbox=atom['bbox'])
            if entry['id'] not in seen:
                blocks.append({'id':f'p{number}_excluded_{entry["id"]}','page':number,'kind':'footer',
                    'bbox':atom['bbox'],'source_ids':[entry['id']],'text':atom['text'],'native_text':atom['text'],
                    'source_type':'native_reordered','heading_path':[],'issues':[],
                    'exclusion_proposed':entry['reason']})
        omitted=set(atoms)-seen-{e['id'] for e in excluded}
        # An omission does not erase source text or get silently labelled complete.
        for source_id in sorted(omitted):
            atom=atoms[source_id]
            blocks.append({'id':f'p{number}_unmapped_{source_id}','page':number,'kind':'paragraph',
                'bbox':atom['bbox'],'source_ids':[source_id],'text':atom['text'],'native_text':atom['text'],
                'source_type':'unreconstructed_native','transcription_status':'pending_structure',
                'heading_path':[],'issues':['native_source_not_reconstructed']})
        unread=object_rows(row,'unread_regions')
        for entry in unread:bbox(entry.get('bbox'))
        if not blocks and expected[number].get('has_visible_content'):
            unread.append({'bbox':[0,0,1,1],'reason':'visible_page_content_has_no_reconstruction'})
        result.append({'page':number,'blocks':blocks,'excluded_sources':excluded,'unread_regions':unread,
            'issues':issues+(['native_sources_omitted'] if omitted else []),
            'status':'partial' if omitted or unread or issues or any(b['issues'] for b in blocks) else 'reconstructed'})
    return {'pages':result}


def zone_plan(answer,blocks):
    index={b['id']:b for b in blocks}; groups=object_rows(answer,'groups'); ids=set(); normalized_groups=[]
    for g in groups:
        if not isinstance(g.get('id'),str) or not g['id'] or g['id'] in ids:raise ContractError('Invalid group identity')
        ids.add(g['id'])
        if g.get('kind') not in {'object','shared','unresolved'}:raise ContractError('Invalid group kind')
        if not isinstance(g.get('name'),str) or not g['name'].strip():raise ContractError('Missing group name')
        for field in ('block_ids','context_block_ids'):
            if not isinstance(g.get(field),list) or any(not isinstance(i,str) or i not in index for i in g[field]):
                raise ContractError('Group cites unknown block')
        keys=list(dict.fromkeys(g['block_ids']+g['context_block_ids']))
        if not keys:raise ContractError('Empty content group')
        normalized_groups.append({**g,'block_ids':list(dict.fromkeys(g['block_ids'])),
            'context_block_ids':list(dict.fromkeys(g['context_block_ids'])),'source_block_ids':keys})
    links=object_rows(answer,'links')
    for link in links:
        a,b=link.get('from'),link.get('to')
        if a not in index or b not in index or a==b or link.get('relation') not in {'continues','header_for','refers_to'}:
            raise ContractError('Invalid document structure link')
        if link['relation']=='continues' and (index[b]['page']<index[a]['page'] or index[b]['page']>index[a]['page']+1):
            raise ContractError('Continuation cannot bridge unselected/nonadjacent pages')
    # Preserve linked paragraphs/headers in the reading context of each group, without declaring ownership.
    for group in normalized_groups:
        keys=set(group['source_block_ids']); changed=True
        while changed:
            before=len(keys)
            for link in links:
                if link['relation'] in {'continues','header_for'} and keys.intersection([link['from'],link['to']]):
                    keys.update([link['from'],link['to']])
            changed=len(keys)>before
        group['source_block_ids']=[b['id'] for b in blocks if b['id'] in keys]
    covered={i for g in normalized_groups for i in g['source_block_ids']}
    missing=[b['id'] for b in blocks if b['kind']!='footer' and b['id'] not in covered]
    if missing:
        key='unassigned_'+content_hash(missing)[:10]
        normalized_groups.append({'id':key,'kind':'unresolved','name':'未分组内容','aliases':[],
            'block_ids':missing,'context_block_ids':[],'source_block_ids':missing,
            'reason':'程序保留规划未覆盖内容，必须继续提取','automatic_fallback':True})
    counts=defaultdict(list)
    for g in normalized_groups:
        for key in g['source_block_ids']:counts[key].append(g['id'])
    return {'groups':normalized_groups,'links':links,'issues':answer.get('issues',[]),
        'coverage':{'content_blocks':sum(b['kind']!='footer' for b in blocks),'unplanned_block_ids':missing,
                    'shared_blocks':{k:v for k,v in counts.items() if len(v)>1},
                    'note':'分组覆盖不是语义提取召回率；共享来源只计一次证据'}}


def extract_claims(answer,blocks,groups,document_id):
    claims=[];invalid=[]
    for original in object_rows(answer,'claims'):
        raw=dict(original)
        if raw.get('kind') in {'calculation','requirement'} and raw.get('kind')==raw.get('role') and not raw.get('target'):
            raw.update(original_kind=raw['kind'],kind='fact')
        try:claims.extend(_extract_claims({'claims':[raw]},blocks,groups,document_id)['claims'])
        except (ContractError,TypeError,KeyError) as exc:
            invalid.append({'raw_claim':original,'reason':str(exc),'stage':'claim_validation'})
    return {'claims':claims,'unresolved':list(answer.get('unresolved',[]))+invalid,'invalid_claims':invalid}


def _extract_claims(answer,blocks,groups,document_id):
    index={b['id']:b for b in blocks}; group_ids={g['id'] for g in groups}; result=[]
    for raw in object_rows(answer,'claims'):
        for key in ('subject','kind','field','value','unit','unit_basis','scope','time','condition','role','target'):
            if not isinstance(raw.get(key),str):raise ContractError('Invalid claim field: '+key)
        if raw['kind'] not in {'fact','relation'} or not raw['field'].strip() or not raw['value'].strip():
            raise ContractError('Invalid statement')
        if raw['role'] not in {'observation','calculation','requirement','design','forecast','history','reference','unknown'}:
            raise ContractError('Invalid statement role')
        if not isinstance(raw.get('group_ids'),list) or any(i not in group_ids for i in raw['group_ids']):
            raise ContractError('Claim cites unknown group')
        evidence=[]; errors=[]
        for e in object_rows(raw,'evidence'):
            b=index.get(e.get('block_id')); quote=e.get('quote')
            if (not b or not isinstance(quote,str) or not quote.strip()
                    or normalized(quote) not in normalized(b['text']) or not literal_supported(quote,b['text'])):
                errors.append('quote_not_in_provided_block');continue
            evidence.append({'block_id':b['id'],'quote':quote,'page':b['page'],'bbox':b['bbox'],
                             'source_ids':b['source_ids'],'source_type':b['source_type']})
        if not evidence:errors.append('missing_source_evidence')
        cited='\n'.join(e['quote'] for e in evidence)
        if not any(literal_supported(raw['value'],e['quote']) for e in evidence):errors.append('value_not_literal_in_evidence')
        if raw['unit'] and (not raw['unit_basis'] or normalized(raw['unit_basis']) not in normalized(cited)):
            errors.append('unit_basis_not_in_evidence')
        if raw['unit'] and not unit_supported(raw['unit'],[raw['unit_basis']]):errors.append('unit_not_supported_by_basis')
        if not raw['subject']:errors.append('unresolved_subject')
        parent=raw.get('parent_subject','')
        if not isinstance(parent,str):raise ContractError('Invalid parent subject')
        parent_evidence=[]
        for e in object_rows({'parent_evidence':raw.get('parent_evidence',[])},'parent_evidence'):
            b=index.get(e.get('block_id'));quote=e.get('quote')
            if not b or not isinstance(quote,str) or not quote.strip() or normalized(quote) not in normalized(b['text']) or not literal_supported(quote,b['text']):
                errors.append('invalid_parent_evidence');continue
            parent_evidence.append({'block_id':b['id'],'quote':quote,'page':b['page'],'bbox':b['bbox'],
                'source_ids':b['source_ids'],'source_type':b['source_type']})
        if parent and not any(normalized(parent) in normalized(e['quote']) for e in parent_evidence):errors.append('parent_not_in_evidence')
        owners=[g for g in groups if g['id'] in raw['group_ids'] and g['kind']=='object']
        if raw['subject'] and owners and not parent and all(normalized(raw['subject'])!=normalized(g['name']) for g in owners):
            errors.append('parent_not_explicit')
        identity_scope=parent or '|'.join(sorted(raw['group_ids']))
        item={**raw,'document_id':document_id,'evidence':evidence,'quality_issues':errors,
              'parent_subject':parent,'parent_evidence':parent_evidence,
              'subject_key':content_hash([document_id,identity_scope,raw['subject']]),
              'status':'pending','reason':'awaiting_source_and_ownership_review','object':raw['subject'],
              'page':evidence[0]['page'] if evidence else None,'quote':cited,'origin_groups':raw['group_ids']}
        item['id']='d_'+content_hash([document_id,raw])[:22]
        result.append(item)
    return {'claims':result,'unresolved':answer.get('unresolved',[])}


def reconcile(answer,claims,blocks,groups,document_id):
    reviews=object_rows(answer,'reviews'); known={c['id'] for c in claims}
    original={c['id']:c for c in claims}
    if sorted(r.get('claim_id','') for r in reviews)!=sorted(known):
        raise ContractError('Every supplied claim needs exactly one review')
    for r in reviews:
        if r.get('verdict') not in {'supported','uncertain','contradicted'} or not isinstance(r.get('reason'),str):
            raise ContractError('Invalid claim review')
        additional=r.get('supporting_evidence',[])
        if not isinstance(additional,list) or any(not isinstance(e,dict) for e in additional):
            raise ContractError('Invalid supplementary evidence')
        if additional:
            old=original[r['claim_id']]
            # Re-run the same source/value/unit checks. Review verdicts never bypass them.
            updated=_extract_claims({'claims':[{**old,'evidence':old['evidence']+additional}]},blocks,groups,document_id)['claims'][0]
            r['rechecked_evidence']={k:updated[k] for k in ('evidence','quote','quality_issues')}
        correction=r.get('correction',{})
        allowed={'subject','parent_subject','parent_evidence','scope','role','condition','time','unit_basis'}
        if not isinstance(correction,dict) or set(correction)-allowed:raise ContractError('Invalid review correction fields')
        if correction:
            old=original[r['claim_id']]
            updated=_extract_claims({'claims':[{**old,**correction,'evidence':old['evidence']+additional}]},blocks,groups,document_id)['claims'][0]
            if 'subject' in correction and not any(normalized(updated['subject']) in normalized(e['quote']) for e in updated['evidence']+updated['parent_evidence']):
                updated['quality_issues'].append('corrected_subject_not_in_evidence')
            updated['object']=updated['subject']
            r['rechecked_claim']={k:updated[k] for k in (*allowed,'object','subject_key','evidence','quote','quality_issues')}
    additions=extract_claims({'claims':object_rows(answer,'additions'),'unresolved':[]},blocks,groups,document_id)
    return {'reviews':reviews,'additions':additions['claims'],'invalid_additions':additions['invalid_claims'],
        'issues':answer.get('issues',[]),'repair_requests':answer.get('repair_requests',[])}


def consolidate(claims):
    result=[]; seen={}
    for raw in claims:
        key=tuple(normalized(raw.get(k,'')) for k in ('subject_key','subject','kind','field','value','unit','scope','time','condition','role','target'))
        if key in seen:
            old=seen[key]; old['duplicate_claim_ids'].append(raw['id'])
            old['origin_groups']=list(dict.fromkeys(old['origin_groups']+raw['origin_groups']))
            evidence={(e['block_id'],e['quote']):e for e in old['evidence']+raw['evidence']}
            old['evidence']=list(evidence.values())
            # A conflicting review never becomes supported by duplicate counting.
            if raw['status']!=old['status'] or raw['quality_issues']:
                old.update(status='pending',reason='duplicate_review_disagreement')
            continue
        item={**raw,'evidence':list(raw['evidence']),'origin_groups':list(raw['origin_groups']),'duplicate_claim_ids':[]}
        seen[key]=item;result.append(item)
    buckets=defaultdict(list)
    for row in result:
        buckets[tuple(normalized(row.get(k,'')) for k in ('subject_key','subject','kind','field','unit','scope','time','condition','role','target'))].append(row)
    for rows in buckets.values():
        if len({normalized(r['value']) for r in rows})>1:
            for row in rows:
                row.update(status='pending',reason='parallel_values_require_review')
                row['quality_issues']=list(dict.fromkeys(row['quality_issues']+['possible_conflict']))
    return result
