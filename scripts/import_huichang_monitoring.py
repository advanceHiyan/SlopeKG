from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from slopekg.monitoring import build_huichang_pilot  # noqa: E402


if __name__ == "__main__":
    result = build_huichang_pilot()
    print(json.dumps({k: result[k] for k in ("status", "batch_id", "files", "observations", "common_days", "risk_eligible")}, ensure_ascii=False, indent=2))
