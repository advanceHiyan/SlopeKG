"""Publish a recorded-time raw-value experiment from two existing Huichang tables."""
import json
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from slopekg.monitoring_baseline import build_baseline_experiment, export_baseline_report

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--html', type=Path, help='另存自包含离线实验报告')
    args = parser.parse_args()
    report = build_baseline_experiment()
    exported = export_baseline_report(args.html) if args.html else None
    print(json.dumps({"run_id": report["run_id"], "boundary": report["boundary"],
                      "offline_report": exported,
                      "series": [{"file_name": s["file_name"], "components": [
                          {k: c[k] for k in ("component", "status", "metrics")} for c in s["components"]]} for s in report["series"]]},
                     ensure_ascii=False, indent=2))
