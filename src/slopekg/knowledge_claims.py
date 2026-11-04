"""Partition chunk attribution and preserve its source-grounded atomic claims."""
from .knowledge_attribution import source_local
from .visual_contract import ContractError


def partition_attribution(answer, batch, units, known, resolve):
    rows=answer.get('assignments')
    if not isinstance(rows,list):
        raise ContractError('Invalid knowledge assignments')
    owners={}
    for chunk in batch:
        for key in chunk['unit_ids']:
            owners.setdefault(key,[]).append(chunk['id'])
    source_order=list(units);positions={key:i for i,key in enumerate(source_order)}
    normalized={c['id']:{'chunk_id':c['id'],'entities':[],'unresolved':[],
                         'attribution_status':'pending'} for c in batch}
    bindings={};seen=set();repairs=0;claims=[]
    quarantined=list(answer.get('quarantined',[])) if isinstance(answer.get('quarantined'),list) else []

    def keep_claim(entity_id,fact,ids):
        if entity_id not in known or not isinstance(fact,dict):
            raise ContractError('Invalid attributed fact identity')
        claims.append({'entity_id':entity_id,'fact':fact,'provided_span_ids':list(dict.fromkeys(ids))})

    # Cached normalized answers retain these claims without another request.
    for claim in answer.get('claims',[]) if isinstance(answer.get('claims',[]),list) else []:
        try:
            ids=[resolve(key,units,owners) for key in claim['provided_span_ids']]
            keep_claim(claim['entity_id'],claim['fact'],ids)
        except (ContractError,KeyError,TypeError):
            quarantined.append({'reason':'invalid_cached_claim','output':claim})

    for row in rows:
        label=row.get('chunk_id') if isinstance(row,dict) and isinstance(row.get('chunk_id'),str) else None
        if label in normalized and row.get('attribution_status')!='pending':
            seen.add(label)
        if not isinstance(row,dict) or not isinstance(row.get('entities'),list):
            quarantined.append({'reason':'invalid_chunk_binding','output':row})
            if label in normalized:normalized[label]['unresolved'].append('invalid_chunk_binding')
            continue
        if label in normalized and isinstance(row.get('unresolved',[]),list):
            normalized[label]['unresolved'].extend(row.get('unresolved',[]))
        for binding in row['entities']:
            try:
                if not isinstance(binding,dict):raise ContractError('Invalid chunk binding')
                entity_id=binding.get('entity_id')
                if not isinstance(entity_id,str) or entity_id not in known:raise ContractError('Unknown attributed identity')
                if not isinstance(binding.get('span_ids'),list):raise ContractError('Invalid source identifiers')
                ids=[resolve(key,units,owners) for key in binding['span_ids']]
                ranges=binding.get('span_ranges',[])
                if not isinstance(ranges,list):raise ContractError('Invalid source ranges')
                for span_range in ranges:
                    if not isinstance(span_range,dict):raise ContractError('Invalid source range')
                    start=resolve(span_range.get('start_id'),units,owners)
                    end=resolve(span_range.get('end_id'),units,owners)
                    if positions[start]>positions[end]:raise ContractError('Reversed source range')
                    expanded=source_order[positions[start]:positions[end]+1]
                    if any(key not in owners for key in expanded):raise ContractError('Source range crosses unprovided source')
                    ids.extend(expanded)
                ids=list(dict.fromkeys([*ids,*(h for key in ids for h in units[key].get('header_ids',[]) if h in owners)]))
            except (ContractError,KeyError,TypeError) as exc:
                quarantined.append({'reason':str(exc),'output':binding})
                if label in normalized:normalized[label]['unresolved'].append('invalid_binding_quarantined')
                continue
            for key in ids:
                for owner in owners[key]:
                    seen.add(owner);repairs+=owner!=label
                    slot=bindings.setdefault((owner,entity_id),{'entity_id':entity_id,'span_ids':[],'reason':[]})
                    if key not in slot['span_ids']:slot['span_ids'].append(key)
                    reason=binding.get('reason','')
                    if isinstance(reason,str) and reason and reason not in slot['reason']:slot['reason'].append(reason)
                    if not source_local(known[entity_id],units[key],units):
                        issue={'span_id':key,'entity_id':entity_id,'reason':'numeric_source_has_no_local_object_anchor'}
                        if issue not in normalized[owner]['unresolved']:normalized[owner]['unresolved'].append(issue)
            facts=binding.get('facts',[])
            if not isinstance(facts,list):
                quarantined.append({'reason':'invalid_attributed_facts','output':binding})
                if label in normalized:normalized[label]['unresolved'].append('invalid_attributed_facts')
                continue
            for fact in facts:
                try:keep_claim(entity_id,fact,ids)
                except ContractError as exc:quarantined.append({'reason':str(exc),'output':fact})
    for (owner,_),binding in bindings.items():
        normalized[owner]['entities'].append({**binding,'reason':'；'.join(binding['reason'])})
    for key in seen:
        normalized[key]['attribution_status']='partial' if normalized[key]['unresolved'] else 'completed'
    unique=[]
    for claim in claims:
        if claim not in unique:unique.append(claim)
    prior_repairs=answer.get('source_chunk_repairs',0)
    repairs=max(repairs,prior_repairs if type(prior_repairs) is int else 0)
    return {'assignments':list(normalized.values()),'source_chunk_repairs':repairs,
            'claims':unique,'quarantined':quarantined}
