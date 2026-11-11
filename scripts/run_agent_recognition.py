"""Explicit fifth-route launcher; no model call for --prepare-only/--preflight-only."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from slopekg.agent_pipeline import AgentPipeline
from slopekg.agent_settings import AgentSettings
from slopekg.config import PATHS
from slopekg.pipeline_lock import pipeline_run_guard


def main():
    parser=argparse.ArgumentParser(description='自主 Agent：DSH SDK第五路')
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--input-pack',type=Path)
    group.add_argument('--pdf',type=Path,action='append')
    parser.add_argument('--model-profile',type=Path,default=ROOT/'config/models/deepseek-knowledge-max.json')
    parser.add_argument('--output-dir',type=Path)
    parser.add_argument('--max-calls',type=int,default=None,help='Optional explicit limit; default unlimited')
    parser.add_argument('--timeout-seconds',type=int,default=None,help='Optional explicit deadline; default unlimited')
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--preflight-only',action='store_true')
    parser.add_argument('--resume-run',help='Resume drafts from a failed/partial/cancelled project Agent run; identical source inputs required')
    args=parser.parse_args()
    pipeline=AgentPipeline(PATHS,args.model_profile,output_dir=args.output_dir,
        settings=AgentSettings(max_calls=args.max_calls,timeout_seconds=args.timeout_seconds))
    with pipeline_run_guard(PATHS.output_dir/'.pipeline.lock'):
        result=pipeline.run(args.pdf,input_pack=args.input_pack,prepare_only=args.prepare_only,preflight_only=args.preflight_only,resume_from=args.resume_run)
    print(json.dumps({k:result.get(k) for k in ['run_id','processing_status','summary','usage','elapsed_seconds','errors','run_directory']},ensure_ascii=False,indent=2))
    return 0 if result['processing_status'] in {'completed','prepared','ready'} else 1

if __name__=='__main__':raise SystemExit(main())
