"""Fixed local read-only evaluation command used by check_outputs, not a shell tool."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'src'))
from slopekg.config import PATHS
from slopekg.agent_pipeline import load, validate_outputs
from slopekg.standard_assessment.agent_preview import preview_submission


def check_delivery(workspace, audit):
    manifest = load(workspace / 'manifest.json')
    calls = [json.loads(line) for line in audit.read_text(encoding='utf-8').splitlines() if line.strip()]
    checked = validate_outputs(workspace, manifest, calls)
    recognition = {'facts': len(checked['facts']), 'objects': len(checked['objects']),
                   'errors': checked['errors']}
    if checked['errors']:
        return {'ok': False, 'stage': 'recognition', 'recognition': recognition,
                'errors': checked['errors']}
    try:
        result = preview_submission(PATHS, workspace)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {'ok': False, 'stage': 'assessment', 'recognition': recognition, 'errors': [str(exc)]}
    return {'ok': True, 'stage': 'ready', 'recognition': recognition, 'errors': [],
            'assessment': result}


def main():
    try:
        if len(sys.argv) not in (2, 3):
            raise ValueError('Expected a task workspace and optional audit path')
        workspace = Path(sys.argv[1]).resolve(strict=True)
        if not workspace.is_relative_to(ROOT) or workspace.name != 'workspace':
            raise ValueError('Expected a project-owned Agent workspace')
        audit = (Path(sys.argv[2]) if len(sys.argv) == 3 else
                 workspace.parent / 'control/tool_calls.jsonl').resolve(strict=True)
        if not audit.is_relative_to(workspace.parent) or audit.name != 'tool_calls.jsonl':
            raise ValueError('Expected the audit log for this Agent workspace')
        result = check_delivery(workspace, audit)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = {'ok': False, 'stage': 'verification', 'errors': [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
