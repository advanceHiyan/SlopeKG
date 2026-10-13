from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from slopekg.inspection_history import build_inspection_history

if __name__ == '__main__':
    catalog = build_inspection_history()
    print(json.dumps({k: catalog[k] for k in ['status', 'count', 'counts_by_type', 'start', 'end', 'risk_eligible']}, ensure_ascii=False, indent=2))
