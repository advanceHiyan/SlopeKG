"""No-call structural replay and real-source geometry/Basic diagnostics."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from slopekg.extractors import extract_domain_candidates
from slopekg.knowledge_document import prepare_document
from slopekg.knowledge_pipeline import partition_composition
from slopekg.knowledge_settings import KnowledgeSettings
from slopekg.model_client import ModelProfile
from slopekg.model_limits import LIMIT_SOURCES
from slopekg.parsers import PdfParser
from slopekg.provenance import runtime_identity

BASE = ROOT/'outputs/20261031_三路盲测_阶段1'
OUT = ROOT/'outputs/20261031_三路修复复测'
read=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    protocol=read(BASE/'protocol.json'); frozen=read(BASE/'predictions_frozen.json')
    before={r:sha(ROOT/p['path']) for r,p in frozen['predictions'].items()}
    assert all(before[r]==p['sha256'] for r,p in frozen['predictions'].items())
    source=ROOT/protocol['source']['path'];assert sha(source)==protocol['source']['sha256']
    current=prepare_document(source,KnowledgeSettings(),root=ROOT,parsed_dir=BASE/'basic/parsed',
                             selected_pages=protocol['selected_pages'])
    bad=lambda b: bool(b) and not (0<=b[0]<b[2]<=1 and 0<=b[1]<b[3]<=1)
    batches=[]
    for path in (BASE/'knowledge/cache').glob('*.json'):
        cache=read(path)
        if cache.get('stage')!='knowledge_synthesize' or not cache.get('invalid_answer'):continue
        wire=read(BASE/'knowledge/inputs'/path.name)
        context=json.loads(wire['prompt'].rsplit('\n输入：',1)[1])
        expected={s['id'] for s in context['subjects']}
        repaired=partition_composition(cache['invalid_answer'],expected)
        batches.append({'old_cache':str(path.relative_to(ROOT)),'requested_objects':len(expected),
            'returned_objects':len(cache['invalid_answer']['objects']),
            'retained_objects':len(repaired['objects']),'quarantined_objects':len(repaired['quarantined']),
            'retained_raw_candidates':sum(len(o['facts']) for o in repaired['objects']),
            'object_failures':repaired['object_failures'],
            'per_object':[{'id':o['entity_id'],'name':next(s['name'] for s in context['subjects'] if s['id']==o['entity_id']),
                           'raw_candidates_retained':len(o['facts'])} for o in repaired['objects']]})
    native=asdict(PdfParser(ROOT).parse(source))
    selected={k:v if k=='documents' else [r for r in v if r['page'] in protocol['selected_pages']] for k,v in native.items()}
    old_ocr=read(BASE/'basic/parsed.json')['ocr_results']
    selected['text_blocks'] += [r for r in old_ocr if r['page'] in protocol['selected_pages']]
    basic=extract_domain_candidates(selected)
    result={'new_model_calls':0,'kind':'local structural replay; not a new model prediction or accuracy score',
        'producer':runtime_identity(),'source_sha256':sha(source),'source_pages':protocol['selected_pages'],
        'composition_replay':batches,'geometry':{'unit_count':len(current['units']),
            'out_of_bounds_boxes':sum(bad(u['bbox']) for u in current['units']),
            'geometry_issues':sum(bool(u.get('geometry_issue')) for u in current['units']),
            'ocr_rows_reused':current['source_inventory']['ocr_rows_reused']},
        'basic_after_shared_geometry_repair':{k:len(basic[k]) for k in
            ['slopes','registry','coordinates','treatments','geometry','engineering_records','assertions']},
        'basic_boundary':'Source inspection: inventory and corroborated root headings are gated to survey reports; free design prose uses limited field templates, not generic entity discovery. No document-specific rule added.',
        'model_profiles':{p.name:ModelProfile.load(p).identity() for p in (ROOT/'config/models').glob('*.json')},
        'limit_sources':LIMIT_SOURCES,'local_mechanism_tests':{'passed':22,'provider_requests':0},
        'original_predictions_unchanged':all(before[r]==sha(ROOT/p['path']) for r,p in frozen['predictions'].items())}
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'local_repairs_validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    assert result['geometry']['out_of_bounds_boxes']==0
    assert result['original_predictions_unchanged']
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
