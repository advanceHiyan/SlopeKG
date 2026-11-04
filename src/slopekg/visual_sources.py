"""Source evidence links and completeness diagnostics, without guessed facts."""
import re
from .visual_evidence import literal_supported


def recompose_dimension_field(fact):
    """Keep an explicitly supplied measurement gloss in the candidate field.

    This expands a short label, never drops a qualifier or infers a value.
    Unrelated/conflicting quantities remain blocked by the original gate.
    """
    quantities={b.get('measurement',{}).get('quantity','').strip()
                for b in fact.get('source_bindings',[]) if b.get('kind')=='dimension'}
    if len(quantities)!=1:return
    quantity=next(iter(quantities));field=fact.get('field','')
    base=re.split(r'[（(]',quantity,maxsplit=1)[0].strip()
    normalize=lambda s:re.sub(r'\s+','',s).casefold()
    if quantity.endswith((')', '）')) and normalize(base)==normalize(field) and normalize(quantity)!=normalize(field):
        fact['original_model_field']=field
        fact['field']=quantity
        fact['field_recomposition']='explicit_bound_measurement_quantity; semantic review still required'


def dimension_semantic_issues(dimension, fact=None):
    """A numeric label is a transcription until its measurement is specified."""
    meaning=dimension.get('measurement')
    if not isinstance(meaning,dict) or any(not isinstance(meaning.get(k),str) or not meaning[k].strip()
                                         for k in ('object','quantity','basis')):
        return ['dimension_meaning_unresolved']
    if fact is not None and meaning['quantity'].strip()!=fact['field'].strip():
        return ['dimension_field_not_recomposed']
    return []


def identifier_atoms(value):
    return set(re.findall(r'(?<![A-Za-z0-9])[A-Za-z]+[A-Za-z0-9.+-]*\d[A-Za-z0-9.+-]*|(?<!\d)\d{5,}(?!\d)', value))


def identity_supported(fact, fragments):
    # Numeric tokens are never joined across cells to invent an identifier.
    atoms=identifier_atoms(fact['object']+' '+fact['scope'])
    return all(any(literal_supported(atom,q) for q in fragments) for atom in atoms)


def fact_quality_issues(fact, fragments):
    issues=[]
    for binding in fact.get('source_bindings',[]):
        if binding.get('kind')=='dimension':
            issues.extend(dimension_semantic_issues(binding,fact))
    if not identity_supported(fact, fragments):
        issues.append('object_identifier_not_grounded')
    basis=fact.get('unit_basis','')
    if basis and not any(literal_supported(basis,q,unit=True) or
                         any(literal_supported(basis,denominator,unit=True)
                             for denominator in re.split(r'[/／]',q,maxsplit=1)[1:]) for q in fragments):
        issues.append('unit_basis_not_grounded')
    if re.search(r'单价|unit\s*price',fact['field'],re.I) and not basis:
        issues.append('unit_basis_missing')
    return issues


def content_coverage(answer):
    """Track explicit validated links; equal numbers are not object bindings."""
    result=[]
    for ti,table in enumerate(answer.get('tables',[])):
        for cell in table['cells']:
            if cell['text'].strip():
                result.append({'kind':'cell','table_index':ti,'row':cell['row'],'column':cell['column'],
                    'text':cell['text'],'bbox':table['bbox'],**_link(cell,answer['facts'])})
    for i,d in enumerate(answer.get('dimensions',[])):
        link=_link(d,answer['facts'])
        issues=[] if d.get('disposition')=='reference_only' else dimension_semantic_issues(d)
        if issues:
            link.update(accounted=False,semantic_issues=issues)
        result.append({'kind':'dimension','dimension_index':i,'text':d['text'],'bbox':d['bbox'],**link})
    return result


def fact_source_bindings(answer):
    """Reverse explicit content links, retaining exact row/column addresses.

    Never infer a link because two cells contain the same number. Header paths
    are model hypotheses and accompany the actual cell text for image review.
    """
    result=[[] for _ in answer['facts']]
    for ti, table in enumerate(answer['tables']):
        for cell in table['cells']:
            if cell.get('disposition') != 'fact':
                continue
            binding={'kind':'table_cell','table_index':ti,'caption':table['caption'],
                     'bbox':table['bbox'],**{k:cell[k] for k in
                         ('row','column','row_span','column_span','text','header')}}
            for index in cell.get('fact_indices',[]):
                result[index].append(dict(binding))
    for di, dimension in enumerate(answer['dimensions']):
        if dimension.get('disposition') != 'fact':
            continue
        binding={'kind':'dimension','dimension_index':di,'text':dimension['text'],'bbox':dimension['bbox']}
        if 'measurement' in dimension:
            binding['measurement']=dimension['measurement']
        for index in dimension.get('fact_indices',[]):
            result[index].append(dict(binding))
    return result


def _link(item,facts):
    indices=item.get('fact_indices',[])
    disposition=item.get('disposition','unresolved')
    # A model cannot mark an unrelated fact index as coverage of this cell.
    proof=all(any(literal_supported(item['text'],q) for q in
                  [facts[i]['quote'],*facts[i].get('support_quotes',[])]) for i in indices)
    accounted=(bool(indices) and disposition=='fact' and proof) or (disposition=='reference_only' and bool(item.get('reason')))
    return {'fact_indices':indices,'disposition':disposition,'reason':item.get('reason',''),'accounted':accounted}


def related_sources(report, page, region_id, assignment, max_page_distance):
    """Only actual reads on this page or explicitly model-linked neighbour pages."""
    linked={r['region_id'] for r in assignment.get('source_context_regions',[])
            if abs(r['page']-page) <= max_page_distance}
    result=[]
    for state in report['pages']:
        for r in state['regions']:
            if (r['id']==region_id and state['page']==page) or not r.get('reading'):
                continue
            if state['page']==page or r['id'] in linked:
                result.append({'region_id':r['id'],'page':state['page'],'bbox':r['bbox'],
                    'is_table':bool(r['reading']['tables']),'transcript':r['reading']['transcript']})
    return result


def unaccounted_content(regions):
    latest={}
    for region in regions:
        for item in region.get('content_coverage',[]):
            key=(item['kind'],tuple(item['bbox']),item.get('table_index'),item.get('row'),
                 item.get('column'),item.get('dimension_index'),item['text'])
            latest[key]=item
    return [item for item in latest.values() if not item['accounted']]
