"""Budget a complete reading cycle before expanding model-proposed regions."""
from copy import deepcopy


def future_call_reserve(states,remaining,current_minimum,*,window_pages,independent_review):
    """Reserve affordable later windows before optional reviews/zoom/audits.

    Each page gets one primary region. This protects source coverage without
    promising that dense semantics fit the same minimum call estimate.
    """
    available=max(0,remaining-current_minimum);reserved=0
    for start in range(0,len(states),window_pages):
        window=states[start:start+window_pages]
        layouts=sum('card' not in s for s in window)
        primary=sum(bool(s['card']['regions']) if 'card' in s else True for s in window)
        cost=layouts+1+len(window)+(2+int(independent_review))*primary
        if reserved+cost>available:break
        reserved+=cost
    return reserved


def plan_window(states, remaining_calls, *, independent_review):
    cycle=2+int(independent_review)  # read, optional blind read, verification
    overhead=1+len(states)  # segmentation plus at least one coverage audit/page
    count=sum(len(s['card']['regions']) for s in states)
    before=overhead+cycle*count
    coalesced=[]
    if before>remaining_calls:
        for state in states:
            regions=state['card']['regions']
            if len(regions)<2:
                continue
            state['original_layout_regions']=deepcopy(regions)
            boxes=[r['bbox'] for r in regions]
            combined={'id':'page_source','kind':'other',
                'bbox':[min(b[0] for b in boxes),min(b[1] for b in boxes),
                        max(b[2] for b in boxes),max(b[3] for b in boxes)],
                'objects':list(dict.fromkeys(o for r in regions for o in r.get('objects',[]))),
                'summary':'Combined page source; preserve separate objects, tables and measurement contexts.',
                'source_region_ids':[r['id'] for r in regions],
                'anchor_ids':list(dict.fromkeys(a for r in regions for a in r.get('anchor_ids',[]))),
                'geometry_source':'union_of_original_source_regions',
                'source_regions':deepcopy(regions)}
            state['card']['regions']=[combined]
            coalesced.append(state['page'])
    count=sum(len(s['card']['regions']) for s in states)
    minimum=overhead+cycle*count
    return {'remaining_call_limit':remaining_calls,'original_minimum_estimate':before,
        'primary_regions':count,'minimum_cycle_estimate':minimum,
        'minimum_cycle_fits':minimum<=remaining_calls,'coalesced_pages':coalesced,
        'note':'Planning lower bound, not an accuracy/cost claim. Dense verification/audit shards and uncertainty recovery may need more calls. All original regions retained; quality gates unchanged.'}
