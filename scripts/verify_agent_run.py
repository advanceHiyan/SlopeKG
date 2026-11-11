"""Offline receipt and structural verification; no semantic answers or API calls."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from slopekg.agent_pipeline import load,sha,validate_outputs
from slopekg.agent_gateway import dump


def verify(run_file):
    run=load(run_file);folder=Path(run['run_directory']);workspace=folder/'workspace';control=folder/'control'
    protocol=load(control/'protocol.json');manifest=load(workspace/'manifest.json')
    calls=[json.loads(s) for s in (control/'tool_calls.jsonl').read_text(encoding='utf-8').splitlines() if s.strip()]
    checked=validate_outputs(workspace,manifest,calls)
    receipts=load(control/'exchanges/receipts.json')
    checks={
        'all_input_hashes_match':all(sha(workspace/f['path'])==f['sha256'] for d in manifest['documents'] for f in d['files']),
        'frozen_source_hashes_match':all(sha(control/'source'/p)==v for p,v in protocol['code_sha256'].items()),
        'all_responses_complete':all(r['status']=='complete' and not r.get('truncated') for r in receipts),
        'all_requests_max_output':all(r.get('requested_max_output_tokens')==run['model']['max_output_tokens'] for r in receipts),
        'all_usage_available':all(r.get('usage') is not None for r in receipts),
    }
    return {'run_id':run['run_id'],'scope':{'documents':len(manifest['documents']),
            'selected_pages':sum(len(d['selected_physical_pages']) for d in manifest['documents']),
            'boundary':'仅所提供资料范围的结构及回执核查，不是语义验收'},
        'processing_status':run['processing_status'],'model':run['model'],'settings':run['settings'],
        'summary':run['summary'],'usage':run['usage'],'elapsed_seconds':run['elapsed_seconds'],
        'runtime':load(control/'compatibility.json'),'checks':checks,'structural_errors':checked['errors'],
        'actual_image_pages':checked['actual_image_pages'],
        'prediction_sha256':{str(p.relative_to(folder)):sha(p) for p in sorted((workspace/'outputs').iterdir()) if p.is_file()},
        'generation_source_sha256':protocol['code_sha256'],
        'current_source_differs':[p for p,v in protocol['code_sha256'].items() if not (ROOT/p).exists() or sha(ROOT/p)!=v],
        'note':'生成代码单独冻结。核验不改写原始输出，所有事实仍是待核候选。',
        'new_api_calls_by_verifier':0}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('run_file',type=Path);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();v=verify(args.run_file);dump(args.output,v)
    print(json.dumps(v,ensure_ascii=False,indent=2))
    raise SystemExit(0 if all(v['checks'].values()) and not v['structural_errors'] else 1)
