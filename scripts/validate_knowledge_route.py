"""Reproducible, isolated same-file diagnostic; never publishes another route's output.

The protocol is saved BEFORE model runs. Its checkpoints were already inspected
in this project, so this is a diagnostic comparison, not an independent blind test.
"""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from slopekg.storage import write_json, read_json
from slopekg.model_client import ModelProfile, CompatibleVisionClient
from slopekg.knowledge_pipeline import KnowledgePipeline
from slopekg.knowledge_settings import KnowledgeSettings
from slopekg.visual_pipeline import VisualPipeline, VisualSettings
from slopekg.parsers import PdfParser
from slopekg.extractors import extract_domain_candidates


def checkpoints():
    # Sample-specific expectations belong only to the evaluation fixture, never
    # production discovery, attribution, identity or synthesis code.
    return [
        {'id':'A-height','object':'K2388+976-K2389+036左侧危岩体','field':'最大坡高','value':'28','unit':'m','page':21},
        {'id':'A-length','object':'K2388+976-K2389+036左侧危岩体','field':'长度','value':'60','unit':'m','page':21},
        {'id':'A-lower','object':'K2388+976-K2389+036左侧危岩体','field':'下边坡坡度','value':'55-63','unit':'°','page':21},
        {'id':'A-upper','object':'K2388+976-K2389+036左侧危岩体','field':'上边坡坡度','value':'76-85','unit':'°','page':21},
        {'id':'A-natural','object':'K2388+976-K2389+036左侧危岩体','field':'单个危岩体稳定系数','value':'1.24','condition':'天然','page':22},
        {'id':'A-saturated','object':'K2388+976-K2389+036左侧危岩体','field':'单个危岩体稳定系数','value':'1.05','condition':'饱和','page':22},
        {'id':'A-GPS-X','object':'K2388+976-K2389+036左侧危岩体','field':'GPS01 X','value':'356065.841','page':11},
        {'id':'A-GPS-Y','object':'K2388+976-K2389+036左侧危岩体','field':'GPS01 Y','value':'3314980.333','page':11},
        {'id':'A-W1-length','object':'K2388+976-K2389+036左侧危岩体 / W1','field':'W1长度','value':'14','unit':'m','page':21},
        {'id':'B-natural','object':'K2434+460-K2434+585左侧危岩体','field':'单个危岩体稳定系数','value':'1.23','condition':'天然','page':39},
        {'id':'B-saturated','object':'K2434+460-K2434+585左侧危岩体','field':'单个危岩体稳定系数','value':'1.06','condition':'饱和','page':39},
        {'id':'B-GPS-X','object':'K2434+460-K2434+585左侧危岩体','field':'GPS17 X','value':'355905.344','page':12},
        {'id':'B-runoff','object':'K2434+460-K2434+585左侧危岩体','field':'设计径流量','value':'4.66','unit':'m³/s','page':39},
        {'id':'B-capacity','object':'K2434+460-K2434+585左侧危岩体','field':'截排水沟泄洪能力','value':'5.17','unit':'m³/s','page':39},
        {'id':'B-gutter','object':'K2434+460-K2434+585左侧危岩体','field':'截水沟上底','value':'1.243','unit':'m','page':39},
        {'id':'drawing-dimension','object':'K2389+000 A-A断面','field':'水平尺寸','value':'10.8','unit':'m','page':55},
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['freeze','knowledge','basic','multimodal'])
    parser.add_argument('--pdf', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', type=Path, default=ROOT/'config/models/deepseek-knowledge.json')
    parser.add_argument('--max-calls', type=int, default=500)
    parser.add_argument('--concurrency', type=int, default=3)
    args = parser.parse_args()
    source, out = args.pdf.resolve(strict=True), args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if args.stage == 'freeze':
        target = out/'protocol.json'
        if target.exists():
            raise ValueError('Protocol already exists; do not overwrite a frozen diagnostic')
        write_json(target, {'created_at':datetime.now(timezone.utc).isoformat(),'source':str(source),
            'source_sha256':digest,'source_scope':'one complete 127-page PDF, no second report',
            'scope_note':'Known-sample diagnostic, not blind/generalized precision or recall. Check each object, scope, condition and evidence manually.',
            'native_routes_scope':'all native-text pages; graphical pages recorded unread',
            'visual_pages':[11,12,21,22,23,38,39,55],
            'visual_scope_note':'same source file, selected-page diagnostic; not whole-file visual recall or a same-cost speed comparison',
            'checkpoints':checkpoints()})
        print(str(target));return
    protocol = read_json(out/'protocol.json')
    if not protocol or protocol['source_sha256'] != digest:
        raise ValueError('Missing protocol or source changed')
    started = time.monotonic()
    def progress(percent, stage, message):
        print(json.dumps({'percent':percent,'stage':stage,'message':message},ensure_ascii=False),flush=True)
    if args.stage == 'basic':
        parsed = asdict(PdfParser(ROOT).parse(source, page_callback=lambda n: print('basic page',n,flush=True) if n%10==0 else None))
        write_json(out/'basic/parsed.json', parsed)
        result = extract_domain_candidates(parsed)
        write_json(out/'basic/extracted.json', result)
        print('BASIC',json.dumps(result['stats'],ensure_ascii=False),flush=True)
    else:
        model = CompatibleVisionClient(ModelProfile.load(args.profile),ROOT)
        if args.stage == 'knowledge':
            result = KnowledgePipeline(model,out/'knowledge',settings=KnowledgeSettings(max_calls=args.max_calls,
                model_concurrency=args.concurrency),progress_callback=progress).run([source])
        else:
            result = VisualPipeline(model,out/'multimodal',settings=VisualSettings(max_calls=args.max_calls,
                model_concurrency=args.concurrency),progress_callback=progress).run([source],
                page_selections={str(source):protocol['visual_pages']})
        print('RESULT',json.dumps({k:result.get(k) for k in ('processing_status','summary')},ensure_ascii=False),flush=True)
    write_json(out/(args.stage+'_receipt.json'),{'source_sha256':digest,'elapsed_seconds':time.monotonic()-started,
        'stage':args.stage,'max_calls':args.max_calls,'profile_file':str(args.profile),
        'scope':protocol['visual_pages'] if args.stage=='multimodal' else 'all native source pages',
        'completed_at':datetime.now(timezone.utc).isoformat()})


if __name__ == '__main__':
    main()
