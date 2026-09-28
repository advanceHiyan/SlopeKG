"""Usage: PYTHONPATH=src python scripts/validate_evaluation_partition.py manifest.json"""
import argparse
import json
from pathlib import Path

from slopekg.evaluation_protocol import validate_document_partition


def main():
    parser = argparse.ArgumentParser(description='Reject document leakage before calling an evaluation independent.')
    parser.add_argument('manifest', type=Path)
    args = parser.parse_args()
    result = validate_document_partition(json.loads(args.manifest.read_text(encoding='utf-8')))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['valid_independent_holdout'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
