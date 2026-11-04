"""Import the six explicitly open historical variants into the debug workspace.

Copies saved evidence only. No recognition, OCR, model calls or hidden inputs.
The additional source aliases exist only in this importer process, not the server.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from slopekg.config import PATHS
from slopekg import recognition_debug as debug
from slopekg.debug_history import (capture_snapshot, checked_artifact, create_record,
    get_record, list_records, record_dir, seal_record, update_record)
from slopekg.storage import read_json, write_json

OUT = ROOT / "outputs/知联调试"
ALLOWED = {
    "G359": {"d0c6cf892dec421b3710d23058a29cc54e5fa77fab35eaf6dbf2b5ad45d68e3a"},
    "G209-report": {"48c5da9cf568db6af93fee6ce5df0b579f8c511a762b6c5167505a3403c05c0e"},
    "paper": {"01015fc12c9f8a74b7275a5c6d89685fa258ae73d4d184454944157c520c28b1"},
    "guide": {"113cb4d072120fd77e61c5a9b973be4d62df1af0a56c56701bc64c7a1360a928"},
}
NOTES = {
    "G359": ("G359施工图", "图层厚度漏提、对象归属和角色/单位待审", "facts", "厚度", 0),
    "G209-report": ("G209勘察报告", "255已进入对象档案和重组输入，最终事实漏项", "units", "255", 29),
    "paper": ("滑坡论文", "跨行证据和公式候选仍待审", "gates", "", 4),
    "guide": ("监测指南", "跨行“振动监测”被字面证据门控拒绝", "gates", "振动监测", 12),
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    export = read_json(OUT / "debug_data.json")
    assert {c["id"] for c in export["cases"]} == set(ALLOWED)
    assert sum(len(c["variants"]) for c in export["cases"]) == 6
    rows = []
    existing = list_records(PATHS, limit=100)["records"]
    original_aliases = dict(debug.OPEN_RUNS)
    try:
        for case in export["cases"]:
            for variant in case["variants"]:
                origin_file = debug._safe_location(variant["path"], ROOT)
                assert sha(origin_file) == variant["run_sha256"], "Original prediction has changed"
                origin = read_json(origin_file)
                assert len(origin["documents"]) == 1
                doc = origin["documents"][0]
                assert doc["source_sha256"] in ALLOWED[case["id"]]
                source = debug._safe_location(doc["path"], ROOT)
                assert sha(source) == doc["source_sha256"]
                title, note, stage, query, page = NOTES[case["id"]]
                if case["id"] == "guide" and not origin.get("receipts"):
                    note, stage, query, page = "本地准备失败，尚未发出模型请求", "errors", "", 0
                label = f"[失败复盘] {title} · {variant['label']}"
                import_key = "open-failure:" + variant["run_sha256"]
                old = next((r for r in existing if r.get("import_key") == import_key
                            and r.get("snapshot_ready")), None)
                if old:
                    meta = get_record(PATHS, old["id"])
                    old_view = read_json(record_dir(PATHS, meta["id"]) / "definition.json").get("suggested_view")
                    view = {"stage": stage, "q": query, "page": page}
                    if old_view != view:
                        meta = update_record(PATHS, meta["id"], suggested_view=view,
                            navigation_note="快照中的旧导航页码有笔误；以此处更正导航为准。原始预测、来源坐标与冻结快照均未修改。")
                else:
                    alias = "import-open-" + variant["id"]
                    # Process-local source registration uses the same validation and
                    # snapshot routines as the UI; no backend restart is needed.
                    debug.OPEN_RUNS[alias] = (label, origin_file.relative_to(ROOT).as_posix())
                    definition = {
                        "kind": "open_historical_failure_snapshot",
                        "source": {"case_id": case["id"], "variant": variant["label"],
                            "run_path": variant["path"], "run_sha256": variant["run_sha256"],
                            "input_sha256": doc["source_sha256"],
                            "original_sha256": case["original_sha256"],
                            "tested_original_pages": case["tested_original_pages"],
                            "input_page_to_original_page": variant.get("page_map", {})},
                        "failure_summary": note,
                        "suggested_view": {"stage": stage, "q": query, "page": page},
                        "evidence_note": "历史已保存预测，加载不生成新预测。缓存answer为解析/校验后对象；未保存的供应商原文和输入保持缺失。历史失败不能直接证明当前版本同样失败。",
                        "new_model_requests": 0,
                    }
                    meta = create_record(PATHS, label=label, route="knowledge",
                                         mode="snapshot", definition=definition)
                    try:
                        archived, _ = capture_snapshot(PATHS, meta["id"], route="knowledge",
                            run_id=alias, document=doc["id"])
                        assert archived["documents"][0] == {
                            **doc, "original_source_path": doc.get("original_source_path", doc["path"]),
                            "path": str(record_dir(PATHS, meta["id"]) / "prediction/source.pdf")}
                        destination = record_dir(PATHS, meta["id"]) / "prediction"
                        # Preserve the original run byte-for-byte only if it has no
                        # credential fields; the normal debug copy is always cleaned.
                        assert debug._clean(origin) == origin, "Credential metadata must not be archived"
                        shutil.copy2(origin_file, destination / "original-run.json")
                        assert sha(destination / "original-run.json") == variant["run_sha256"]
                        seal_record(PATHS, meta["id"], run=archived,
                            extra={"import_key": import_key, "case_id": case["id"],
                                   "failure_summary": note, "new_model_requests": 0,
                                   "tags": ["开放失败复盘", "0新API", variant["label"]]})
                        meta = update_record(PATHS, meta["id"], status="completed", progress=100,
                            message="开放历史失败记录已加载；原预测和已保存过程保持不变")
                    except Exception as exc:
                        update_record(PATHS, meta["id"], status="failed", error=str(exc))
                        raise
                run_id = "history-" + meta["id"]
                counts = {s[0]: debug.debug_records(PATHS, route="knowledge", run_id=run_id,
                          document=meta["document_id"], stage=s[0], limit=1)["count"]
                          for s in debug.STAGES["knowledge"]}
                calls = debug.debug_records(PATHS, route="knowledge", run_id=run_id,
                    document=meta["document_id"], stage="calls", limit=100)["rows"]
                details = [debug.debug_call(PATHS, route="knowledge", run_id=run_id,
                    document=meta["document_id"], key=r["id"]) for r in calls]
                checked_artifact(record_dir(PATHS, meta["id"]) / "prediction/original-run.json")
                from urllib.parse import urlencode
                url = "http://127.0.0.1:8766/web/recognition-debug.html?" + urlencode({
                    "route": "knowledge", "run": run_id, "stage": stage, "q": query, "page": page})
                assert debug.debug_records(PATHS, route="knowledge", run_id=run_id,
                    document=meta["document_id"], stage=stage, q=query, page=page)["count"] > 0
                rows.append({"id": meta["id"], "label": meta["label"],
                    "source_sha256": meta["source_sha256"], "run_sha256": variant["run_sha256"],
                    "failure_summary": note, "url": url, "stage_counts": counts,
                    "saved_inputs": sum(bool(c["input"]) for c in details),
                    "saved_normalized_responses": sum(bool(c["cache"]) for c in details),
                    "saved_raw_exchanges": sum(bool(c["exchange"]) for c in details)})
                print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    finally:
        debug.OPEN_RUNS.clear()
        debug.OPEN_RUNS.update(original_aliases)
    write_json(OUT / "workspace_import_receipt.json", {"new_model_requests": 0,
        "hidden_cases_included": 0, "records": rows, "count": len(rows),
        "history_url": "http://127.0.0.1:8766/web/recognition-debug.html?tab=history"})


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden during import")):
        main()
