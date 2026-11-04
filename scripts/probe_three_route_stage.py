"""Local-only persistence probe over the frozen first-stage predictions."""
from dataclasses import replace
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/20261031_三路盲测_阶段1'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=OUT)
    selected=parser.parse_args().output.resolve()
    # The imported risk implementation belongs to its own immutable snapshot.
    global_output=selected
    sys.path.insert(0,str(global_output/'integration_code/src'))
    from slopekg.config import DemoPaths
    from slopekg.standard_assessment import StandardAssessmentService
    from slopekg.standard_assessment.contract import validate_fact
    from slopekg.standard_assessment.store import AssessmentStore
    run_probe(global_output,DemoPaths,StandardAssessmentService,validate_fact,AssessmentStore)


def run_probe(OUT,DemoPaths,StandardAssessmentService,validate_fact,AssessmentStore):
    frozen = read(OUT / 'predictions_frozen.json')
    for route, info in frozen['predictions'].items():
        assert sha(ROOT / info['path']) == info['sha256'], route
    for row in read(OUT / 'integration_code_manifest.json'):
        assert sha(OUT / 'integration_code' / row['path']) == row['sha256']
    folder = OUT / 'integration'
    database = folder / 'assessment.sqlite'
    if database.exists():
        raise RuntimeError('Do not silently repeat the append-only persistence experiment')
    target = folder / 'knowledge-recognition/run.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((OUT / 'knowledge/run.json').read_bytes())
    assert sha(target) == frozen['predictions']['knowledge']['sha256']
    paths = replace(DemoPaths(), root=ROOT, raw_dir=ROOT / 'data/rawPDF',
                    output_dir=folder, parsed_dir=OUT / 'basic/parsed',
                    web_data_dir=OUT / 'basic/web/data')
    # Prove that this check cannot issue another provider request.
    def network_guard(event, args):
        if event in {'socket.connect', 'urllib.Request'}:
            raise RuntimeError('Database check is local-only; model/network calls forbidden')
    sys.addaudithook(network_guard)
    service = StandardAssessmentService(paths, database=database)
    knowledge = read(OUT / 'knowledge/run.json')
    ids = [f['id'] for d in knowledge['documents'] for f in d['facts']]
    imported = service.import_knowledge(ids)
    reread = AssessmentStore(database).export()
    for fact in reread['fact_assertion']:
        validate_fact(fact)
    assert len(reread['fact_assertion']) == len(ids) == len(imported['rows'])
    assert all(f['origin_kind'] == 'knowledge' and f['semantic_kind'] == 'reference'
               and f['scope']['subject']['canonical_object_id'] is None
               and f['review_status'] != 'confirmed' for f in reread['fact_assertion'])
    graph = read(OUT / 'basic/web/data/demo_graph.json')
    slopes = [n['id'] for n in graph['nodes'] if n['type'] == 'Slope']
    public_imports = [name for name in dir(service) if name.startswith('import_')]
    result = {'checked_at': datetime.now(timezone.utc).isoformat(), 'network_calls': 0,
        'database': str(database.relative_to(ROOT)),
        'code_scope': 'separately frozen current standard_assessment snapshot; not the recognition prediction producer',
        'loaded_service': sys.modules[StandardAssessmentService.__module__].__file__,
        'basic': {'slope_targets': len(slopes), 'prepare_executed': False,
                  'reason': 'No Slope target was produced by the selected-page Basic extraction'},
        'knowledge': {'source_candidates': len(ids), 'persisted_and_reopened': len(reread['fact_assertion']),
                      'canonical_bindings': 0, 'reference_semantics_preserved': True,
                      'professional_review_confirmed': 0, 'risk_ready_inputs_claimed': 0},
        'visual': {'public_import_methods': public_imports,
                   'import_visual_available': hasattr(service, 'import_visual'),
                   'import_executed': False, 'boundary': 'Source API capability check only; no fabricated adapter or asserted end-to-end success'},
        'risk_assessment_run': False, 'formal_grade_claimed': False,
        'table_counts': {k: len(v) for k, v in reread.items()}}
    (OUT / 'database_probe.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    (folder / 'database_readback.json').write_text(json.dumps(reread, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
