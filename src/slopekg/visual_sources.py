"""Source evidence links and completeness diagnostics, without guessed facts."""
import re
from .visual_evidence import literal_supported


def identity_supported(fact, fragments):
    # Numeric tokens are never joined across cells to invent an identifier.
    atoms=re.findall(r'(?<![A-Za-z0-9])[A-Za-z]+[A-Za-z0-9.+-]*\d[A-Za-z0-9.+-]*|(?<!\d)\d{5,}(?!\d)',
                     fact['object']+' '+fact['scope'])
    return all(any(literal_supported(atom,q) for q in fragments) for atom in atoms)


def fact_quality_issues(fact, fragments):
    issues=[]
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
        result.append({'kind':'dimension','dimension_index':i,'text':d['text'],'bbox':d['bbox'],**_link(d,answer['facts'])})
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
