"""Evaluation-side diagnostic retrieval, only after all predictions are frozen.

Never constructs a recognition prompt or calls a model. Literal candidate hits
are previews for manual assessment, not automatic pass/fail decisions.
"""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/20261031_三路修复复测'
read=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()


def numbers(value):
    return {Decimal(n) for n in re.findall(r'\d+(?:\.\d+)?',str(value))}


def main():
    if not (OUT/'predictions_frozen.json').exists():
        raise SystemExit('Predictions are not frozen; reference answers were not opened.')
    frozen=read(OUT/'predictions_frozen.json');protocol=read(OUT/'protocol.json')
    for route,info in frozen['predictions'].items():assert sha(ROOT/info['path'])==info['sha256'],route
    for name,digest in protocol['answer_commitments'].items():assert sha(ROOT/name)==digest,name
    core_ids=set(read(ROOT/'.private/evaluation/current/regression_core.json')['point_ids'])
    rows=[json.loads(x) for x in (ROOT/'.private/evaluation/current/test_points.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
    core=[r for r in rows if r['id'] in core_ids and r['source_sha256']==protocol['source']['sha256']
          and set(r['pages'])<=set(protocol['selected_pages'])]
    assert len(core)==protocol['evaluation_side_core_coverage']['fixed_core_count']
    receipt={'opened_at':datetime.now(timezone.utc).isoformat(),'predictions_frozen_at':frozen['frozen_at'],
        'all_prediction_hashes_verified':True,'answer_hashes_unchanged':True,'core_cases':len(core),
        'boundary':'Source-only page selection and metadata-only membership check preceded prediction; reference answers assessed only after prediction freeze. Known development sources, not an independent holdout.',
        'sealed_holdout_read':False}
    (OUT/'evaluation_opened.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'core_cases_after_freeze.json').write_text(json.dumps(core,ensure_ascii=False,indent=2),encoding='utf-8')
    runs={r:read(OUT/r/'run.json') for r in ['basic','knowledge','visual']}
    previews=[]
    for point in core:
        print('CORE',json.dumps({k:point[k] for k in ['id','pages','carrier','granularity','expected','scorable']},ensure_ascii=False))
        expected=point['expected'];target=numbers(expected['value'])
        preview={'point_id':point['id'],'retrieval_only_not_a_judgment':True}
        for route in ['knowledge','visual']:
            facts=[f for d in runs[route]['documents'] for f in d['facts']]
            candidates=[f for f in facts if f.get('page') in point['pages'] and
                        ((target and target<=numbers(f.get('value',''))) or
                         (not target and str(expected['value']) in str(f.get('value',''))))]
            def rank(f):
                return len(set(str(expected['field'])) & set(str(f.get('field',''))))
            candidates=sorted(candidates,key=rank,reverse=True)
            items=[{k:f.get(k) for k in ['id','object','field','value','unit','qualifier','scope','role','status','reason','page']}
                   for f in candidates[:4]]
            preview[route]=items
            print('CANDIDATES',route,json.dumps(items,ensure_ascii=False))
        previews.append(preview)
    (OUT/'literal_candidate_previews_not_scores.json').write_text(json.dumps(previews,ensure_ascii=False,indent=2),encoding='utf-8')
    print('OPEN_RECEIPT',json.dumps(receipt,ensure_ascii=False))


if __name__=='__main__':main()
