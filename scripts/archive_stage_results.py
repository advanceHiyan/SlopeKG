"""Archive a reproducible, self-describing snapshot of the current demo outputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def archive(source: Path, destination: Path, report: Path) -> dict:
    if destination.exists():
        raise FileExistsError(f"阶段成果目录已存在，拒绝覆盖：{destination}")
    destination.mkdir(parents=True)

    for directory in ("parsed", "extracted", "graph", "risk", "share"):
        shutil.copytree(source / directory, destination / directory)

    rule_files = (
        "documents.jsonl", "clauses.jsonl", "formulas.jsonl", "rule_library.json",
        "rules.jsonl", "state.json", "threshold_tables.jsonl",
    )
    for name in rule_files:
        copy_file(source / "rules" / name, destination / "rules" / name)

    metric_files = (
        "evaluation.json", "evaluation.basic.json", "evaluation.deep.json",
        "evaluation_pages.json", "evaluation_engineering.json",
        "pipeline_state.json", "completeness.json", "schema.json", "interfaces.json",
    )
    for name in metric_files:
        copy_file(source / name, destination / "metrics" / name)

    copy_file(source / "web" / "data" / "demo_graph.json", destination / "graph" / "demo_graph.json")
    copy_file(report, destination / "README.md")

    audit_source = ROOT / "tmp" / "pdfs" / "audit_20260915"
    audit_names = (
        "native_g209_p10.png", "native_g344_p10.png", "table_g209_p33.png",
        "review_g351_p44.png", "scan_g347_p2.png", "scan_classification_p10.png",
        "drawing_g209_p83.png", "drawing_g344_p16_title_fixed2.png",
        "review_g347_p14.png", "station_g351_p5.png",
    )
    for name in audit_names:
        candidate = audit_source / name
        if candidate.exists():
            copy_file(candidate, destination / "audit_pages" / name)

    entries = []
    for path in sorted(p for p in destination.rglob("*") if p.is_file()):
        entries.append({
            "path": path.relative_to(destination).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        })
    manifest = {
        "schema_name": "slopekg-stage-result-manifest",
        "schema_version": "1.0",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source": source.relative_to(ROOT).as_posix(),
        "files": entries,
        "file_count": len(entries),
        "total_bytes": sum(row["bytes"] for row in entries),
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "output" / "demo")
    parser.add_argument(
        "--destination", type=Path,
        default=ROOT / "output" / "milestones" / "20260915_parse_quality_review",
    )
    parser.add_argument("--report", type=Path, default=ROOT / "docs" / "阶段成果说明_20260915.md")
    args = parser.parse_args()
    manifest = archive(args.source.resolve(), args.destination.resolve(), args.report.resolve())
    print(json.dumps({
        "destination": str(args.destination.resolve()),
        "file_count": manifest["file_count"],
        "total_bytes": manifest["total_bytes"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
