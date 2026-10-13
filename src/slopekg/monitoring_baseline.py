"""Auditable raw-value baselines, not a displacement/risk warning model.

Only existing daily surface-displacement and crack source tables are selected.
Unknown physical units do not prevent a numeric benchmark, but prevent any
physical interpretation or deployment claim. Actual data arrival times remain
unknown, so this is recorded-time offline replay, not online validation.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any

from .config import PATHS, DemoPaths
from .monitoring import monitoring_catalog, observation_payload, source_file
from .storage import read_json, write_json, verify_file_sha256

VERSION = "raw-monitoring-baseline-1"
SELECTED_FILES = ("02GP01(地表位移)累计位移_日.xlsx", "05LF03(裂缝)累计位移_日.xlsx")
BOUNDARY = "原值离线基线实验，不是已验证的物理位移预测或风险预警。单位、分量、基准、设备归属及实际发布时间待核；不发布未来预报、风险等级、概率或报警。"
PROTOCOL = {
    "version": VERSION, "target": "next_calendar_date_recorded_raw_value",
    "horizon_calendar_days": 1, "lookback_calendar_days": 7,
    "split": "first 60%/next 20%/last 20% of source calendar span",
    "split_role": "chronological_development_replay_not_blind_or_cross_slope_validation",
    "methods": {"persistence": "y[origin]", "recent_drift": "y[origin]+(y[origin]-y[origin-6])/6"},
    "comparison": "identical valid targets with seven consecutive valid source dates",
    "preprocessing": "none; no imputation, scaling, smoothing, outlier removal or zero deletion",
    "availability_assumption": "past recorded observations assumed available at origin; actual publication timestamps unknown",
    "parameter_selection": "fixed before scores; no tuning on validation/test",
    "reference": "https://otexts.com/fpp3/toolbox.html",
}


def experiment_dir(paths: DemoPaths) -> Path:
    return paths.output_dir / "monitoring_experiments"


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _metrics(records: list[dict], method: str, scale: float | None) -> dict:
    if not records:
        return {"count": 0, "mae": None, "rmse": None, "bias": None, "mase": None}
    errors = [r["predictions"][method] - r["actual"] for r in records]
    mae = math.fsum(abs(e) for e in errors) / len(errors)
    return {"count": len(errors), "mae": mae,
            "rmse": math.sqrt(math.fsum(e * e for e in errors) / len(errors)),
            "bias": math.fsum(errors) / len(errors), "mase": mae / scale if scale and scale > 0 else None}


def evaluate_component(rows: list[dict], component: int, *, lookback: int = 7) -> dict:
    """Pure temporal evaluator; component is zero-based. Never use future inputs.

    Keep rejected rows separately. A calendar date with multiple rows is
    ambiguous; it is not averaged or silently reduced to its last value.
    """
    if not isinstance(component, int) or isinstance(component, bool) or component < 0:
        raise ValueError("component须为非负整数")
    if lookback != 7:
        raise ValueError("本版固定7日窗口，不在测试结果上调参")
    by_day: dict[date, list[dict]] = defaultdict(list)
    rejected = []
    for row in rows:
        try:
            day = date.fromisoformat(row["day"])
            stamp = datetime.fromisoformat(row["time"])
            if stamp.date() != day:
                raise ValueError("recorded date mismatch")
        except (ValueError, TypeError, KeyError):
            rejected.append({"id": row.get("id"), "reason": "invalid_recorded_time"})
            continue
        by_day[day].append(row)
    if not by_day:
        return {"status": "insufficient_data", "component": component + 1,
                "rejected": rejected, "predictions": [], "metrics": {}}
    start, end = min(by_day), max(by_day)
    span = (end - start).days + 1
    train_end = start + timedelta(days=span * 3 // 5 - 1)
    validation_end = start + timedelta(days=span * 4 // 5 - 1)
    split = {"start": start.isoformat(), "train_end": train_end.isoformat(),
             "validation_end": validation_end.isoformat(), "end": end.isoformat()}
    if (train_end - start).days + 1 < lookback or validation_end <= train_end or end <= validation_end:
        return {"status": "insufficient_data", "component": component + 1,
                "split": split, "rejected": rejected, "predictions": [], "metrics": {}}
    valid = {}
    for day, candidates in by_day.items():
        for row in candidates:
            values = row.get("components") or []
            reason = ("ambiguous_source_date" if len(candidates) != 1 else
                      "source_quality_flag" if any(f in row.get("quality_flags", []) for f in ("invalid_value", "duplicate_time", "invalid_time")) else
                      "component_unavailable" if component >= len(values) or not _finite(values[component]) else None)
            if reason:
                rejected.append({"id": row.get("id"), "day": day.isoformat(), "reason": reason})
            else:
                valid[day] = row
    train_changes = [abs(valid[d]["components"][component] - valid[d - timedelta(days=1)]["components"][component])
                     for d in sorted(valid) if d <= train_end and d - timedelta(days=1) in valid]
    scale = math.fsum(train_changes) / len(train_changes) if train_changes else None
    train_values = [r["components"][component] for d, r in valid.items() if d <= train_end]
    flags = []
    if train_values and min(train_values) == max(train_values):
        flags.append("constant_training_values_scaled_error_undefined")
    if train_values and all(v == 0 for v in train_values):
        flags.append("all_zero_training_values_not_proof_of_sensor_health")
    predictions = []
    coverage = {s: Counter() for s in ("validation", "test")}
    exclusions = {s: Counter() for s in ("validation", "test")}
    for i in range((train_end - start).days + 1, span):
        target_day = start + timedelta(days=i)
        partition = "validation" if target_day <= validation_end else "test"
        coverage[partition]["calendar_days"] += 1
        target = valid.get(target_day)
        if target is None:
            exclusions[partition]["target_missing_or_invalid"] += 1
            continue
        coverage[partition]["valid_target_records"] += 1
        origin_day = target_day - timedelta(days=1)
        origin = valid.get(origin_day)
        if origin is None:
            exclusions[partition]["previous_date_missing_or_invalid"] += 1
            continue
        coverage[partition]["persistence_evaluable"] += 1
        history = [valid.get(origin_day - timedelta(days=j)) for j in range(lookback - 1, -1, -1)]
        if any(r is None for r in history):
            exclusions[partition]["seven_day_history_missing_or_invalid"] += 1
            continue
        origin_value = origin["components"][component]
        forecast = origin_value + (origin_value - history[0]["components"][component]) / (lookback - 1)
        if not _finite(forecast):
            exclusions[partition]["nonfinite_forecast"] += 1
            continue
        coverage[partition]["common_evaluable"] += 1
        predictions.append({"target_day": target_day.isoformat(), "origin_day": origin_day.isoformat(), "split": partition,
                            "target_id": target["id"], "origin_id": origin["id"], "input_ids": [r["id"] for r in history],
                            "actual": target["components"][component],
                            "predictions": {"persistence": origin_value, "recent_drift": forecast}})
    metrics = {}
    for partition in ("validation", "test"):
        scored = [r for r in predictions if r["split"] == partition]
        methods = {m: _metrics(scored, m, scale) for m in ("persistence", "recent_drift")}
        base_mae, drift_mae = methods["persistence"]["mae"], methods["recent_drift"]["mae"]
        metrics[partition] = {"methods": methods, "coverage": dict(coverage[partition]),
                              "excluded_calendar_targets": dict(exclusions[partition]),
                              "drift_mae_improvement_fraction": 1 - drift_mae / base_mae if base_mae and drift_mae is not None else None}
    return {"status": "evaluated" if predictions else "insufficient_data", "component": component + 1, "split": split,
            "training_scale": scale, "training_scale_pairs": len(train_changes), "flags": flags,
            "source_rows": len(rows), "valid_source_dates": len(valid), "rejected": rejected,
            "metrics": metrics, "predictions": predictions}


def build_baseline_experiment(paths: DemoPaths = PATHS) -> dict:
    catalog = monitoring_catalog(paths)
    series = {s["file_name"]: s for s in catalog.get("series", [])}
    if not all(name in series for name in SELECTED_FILES):
        raise ValueError("先导入会昌八表样例；本实验仅选择地表位移和裂缝两表")
    used = [series[name] for name in SELECTED_FILES]
    inputs = [{k: s[k] for k in ("id", "file_name", "sha256", "row_count")} for s in used]
    implementation_sha = sha256(Path(__file__).read_bytes()).hexdigest()
    encoded = json.dumps({"protocol": PROTOCOL, "sources": inputs, "implementation_sha256": implementation_sha,
                          "source_parser_version": catalog["schema_version"]}, sort_keys=True, ensure_ascii=False).encode("utf-8")
    run_id = sha256(encoded).hexdigest()[:24]
    results, references = [], {}
    for meta in used:
        source_file(meta["id"], paths)
        if len(meta["component_width_counts"]) != 1:
            raise ValueError("所选原值列的分量数量不稳定，请先核实字段版本，不跨版本建模")
        query = {"series_id": [meta["id"]], "start": [meta["first_time"][:10]], "end": [meta["last_time"][:10]],
                 "limit": ["1000"], "include_invalid_time": ["true"]}
        rows = []
        for offset in range(0, meta["row_count"], 1000):
            rows.extend(observation_payload({**query, "offset": [str(offset)]}, paths)["rows"])
        if len(rows) != meta["row_count"]:
            raise ValueError("所选序列行数与导入目录不一致，取消发布实验")
        # This pilot requires one observation per recorded date, not a
        # resampling of high-frequency observations into fabricated daily data.
        for row in rows:
            references[row["id"]] = {k: row.get(k) for k in ("time", "day", "raw_time", "components", "quality_flags", "source")}
        components = [evaluate_component(rows, i) for i in range(len(meta["component_names"]))]
        results.append({"series_id": meta["id"], "file_name": meta["file_name"], "category": meta["category"],
                        "unit": meta["unit"], "component_names": meta["component_names"], "component_meaning_status": meta["component_meaning_status"],
                        "risk_eligible": False, "components": components})
    # Guard the version throughout the read; neither report nor active pointer
    # is published if a selected source changed during the experiment.
    current = {s["id"]: s for s in monitoring_catalog(paths)["series"]}
    if any(current.get(s["id"], {}).get("sha256") != s["sha256"] for s in used):
        raise ValueError("实验期间所选导入版本变化，请重跑")
    for meta in used:
        source_file(meta["id"], paths)
    report = {"schema_version": VERSION, "run_id": run_id, "generated_at": datetime.now(timezone.utc).isoformat(),
              "status": "offline_experiment", "protocol": PROTOCOL, "sources": inputs,
              "monitoring_batch_id": catalog["batch_id"], "boundary": BOUNDARY, "risk_eligible": False,
              "implementation_sha256": implementation_sha, "source_parser_version": catalog["schema_version"],
              "deployment_ready": False, "actual_availability_verified": False,
              "series": results, "source_references": references}
    folder = experiment_dir(paths)
    staging = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    digest = sha256(staging).hexdigest()
    destination = folder / "snapshots" / run_id / f"report-{digest}.json"
    write_json(destination, report)
    published_digest = sha256(destination.read_bytes()).hexdigest()
    pointer = {"schema_version": VERSION, "run_id": run_id, "report_file": destination.relative_to(folder).as_posix(), "report_sha256": published_digest}
    write_json(folder / "catalog.json", pointer)
    return report


def baseline_payload(paths: DemoPaths = PATHS) -> dict:
    pointer = read_json(experiment_dir(paths) / "catalog.json", None)
    if pointer is None:
        return {"status": "not_generated", "series": [], "boundary": BOUNDARY, "risk_eligible": False, "deployment_ready": False}
    if pointer.get("schema_version") != VERSION:
        raise ValueError("实验版本不兼容，请重新运行原值基线实验")
    file = (experiment_dir(paths) / pointer["report_file"]).resolve()
    file.relative_to(experiment_dir(paths).resolve())
    verify_file_sha256(file, pointer.get("report_sha256"))
    report = read_json(file)
    if report.get("implementation_sha256") != sha256(Path(__file__).read_bytes()).hexdigest():
        raise ValueError("基线代码版本已变化，请重跑；未自动使用旧评分")
    current = {s["id"]: s for s in monitoring_catalog(paths).get("series", [])}
    if any(current.get(s["id"], {}).get("sha256") != s["sha256"] for s in report["sources"]):
        raise ValueError("实验所选来源与当前导入版本不一致，请重跑；未自动使用旧评分")
    for source in report["sources"]:
        source_file(source["id"], paths)
    return report


def export_baseline_report(destination: Path, paths: DemoPaths = PATHS) -> dict:
    report = baseline_payload(paths)
    if not report.get("series"):
        raise ValueError("原值基线实验尚未生成")
    template = (paths.web_dir / "monitoring-baseline.html").read_text(encoding="utf-8")
    css = (paths.web_dir / "pages.css").read_text(encoding="utf-8") + '\n' + (paths.web_dir / "monitoring.css").read_text(encoding="utf-8")
    api = (paths.web_dir / "api.js").read_text(encoding="utf-8")
    js = (paths.web_dir / "monitoring-baseline.js").read_text(encoding="utf-8")
    encoded = json.dumps(report, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    template = template.replace('<link rel="stylesheet" href="./pages.css"/><link rel="stylesheet" href="./monitoring.css"/>', '<style>' + css + '</style>')
    template = template.replace('<script src="./api.js"></script><script src="./monitoring-baseline.js"></script>',
        '<script>window.BASELINE_OFFLINE=' + encoded + ';</script><script>' + api.replace('</script', '<\\/script') + '</script><script>' + js.replace('</script', '<\\/script') + '</script>')
    from .storage import _atomic_write_text
    destination.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(destination, template)
    return {"output": str(destination), "bytes": destination.stat().st_size, "risk_eligible": False}


def baseline_interfaces(payload: dict, paths: DemoPaths = PATHS) -> dict:
    try:
        report = baseline_payload(paths)
        status = "implemented_offline_experiment" if report["series"] else "implemented_not_imported"
        description = "原值保持/7日变化基线离线比较；不输出风险或未来预报"
    except (OSError, ValueError, KeyError, TypeError) as exc:
        status, description = "data_unavailable", "基线实验暂不可读：" + str(exc)
    route = {"path": "/api/monitoring/baseline", "method": "GET", "domain": "monitoring_experiment", "status": status, "description": description}
    rows = [r for r in payload.get("interfaces", []) if r["path"] != route["path"]] + [route]
    return {**payload, "interfaces": rows, "status_counts": dict(Counter(r["status"] for r in rows))}
