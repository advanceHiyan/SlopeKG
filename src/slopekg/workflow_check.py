"""Functional smoke checks of published results, without re-parsing or training.

This is a runtime consistency check, not a semantic accuracy or risk acceptance
test. A missing optional data source is reported as waiting, never as a pass.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone, date
from hashlib import sha256
import json
import math
import re
from typing import Any, Callable
from urllib.parse import urlencode
from uuid import uuid4


class WaitingForData(Exception):
    """The function cannot be exercised with the currently published inputs."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def fingerprint(payload: Any) -> str:
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                             allow_nan=False).encode("utf-8")).hexdigest()


def run_workflow_checks(
    get_json: Callable[[str], Any],
    get_bytes: Callable[[str], bytes],
    post_json: Callable[[str, dict], Any] | None = None,
) -> dict[str, Any]:
    """Exercise real API contracts. POSTs, if supplied, only export unique reports.

    Each check runs independently so one failed data source does not hide other
    results. Re-read authoritative inputs at the end to reject mixed versions.
    """
    cache: dict[str, Any] = {}
    checks: list[dict[str, Any]] = []
    exports: list[dict[str, Any]] = []

    def get(path):
        if path not in cache:
            cache[path] = get_json(path)
        return cache[path]

    def check(key, label, action):
        try:
            detail = action()
            status = "passed"
        except WaitingForData as exc:
            status, detail = "waiting", {"message": str(exc)}
        except Exception as exc:
            status, detail = "failed", {"message": f"{type(exc).__name__}: {exc}"}
        checks.append({"id": key, "label": label, "status": status, "detail": detail})

    def documents():
        inventory, graph = get("/api/documents"), get("/api/graph")
        rows = inventory["rows"]
        if not rows:
            raise WaitingForData("尚无主流程PDF，未验证解析版本")
        require(inventory["count"] == len(rows), "PDF目录计数不一致")
        require(all(r["parsed"] and not r["stale"] for r in rows), "有新增或变更PDF尚未解析")
        sources = graph.get("meta", {}).get("pipeline", {}).get("source_documents", [])
        require(len(sources) == len(rows), "活动图谱的来源文档数量与当前目录不一致")
        require({s["file_name"] for s in sources} == {r["file_name"] for r in rows}, "活动图谱与当前PDF集合不一致")
        require(all(s.get("sha256") for s in sources), "活动图谱缺少来源校验和")
        parsed = get("/api/parsed/documents")["rows"]
        expected = {r["file_name"]: r.get("source_sha256") for r in parsed}
        require(all(expected.get(s["file_name"]) == s["sha256"] for s in sources), "活动图谱与逐页解析版本不同")
        return {"pdfs": len(rows), "source_version_checked": True}

    def graph_and_risk():
        graph = get("/api/graph")
        nodes, edges = graph["nodes"], graph["edges"]
        if not nodes:
            raise WaitingForData("尚无活动图谱，未验证档案和研判")
        ids = {n["id"] for n in nodes}
        require(len(ids) == len(nodes), "图谱有重复实体编号")
        require(all(e["source"] in ids and e["target"] in ids for e in edges), "图谱关系指向不存在的实体")
        slopes = {n["id"] for n in nodes if n["type"] == "Slope"}
        require(slopes, "图谱尚未发现边坡")
        listed = get("/api/slopes")
        require(listed["count"] == len(slopes) and {r["id"] for r in listed["rows"]} == slopes, "边坡档案与图谱不一致")
        completeness, risk = get("/api/completeness"), get("/api/risk/screening")
        require(len(completeness["reports"]) == len(slopes) and {r["slope_id"] for r in completeness["reports"]} == slopes, "完整性报告漏项或多项")
        assessments = risk["assessments"]
        require(len(assessments) == len(slopes) and {r["slope_id"] for r in assessments} == slopes, "研判与边坡档案不一致")
        require(risk.get("formal_risk_assessment_enabled") is False, "当前筛查契约不能启用正式风险等级")
        require(all(r.get("formal_risk_level") is None for r in assessments), "筛查结果误含正式风险等级")
        sid = sorted(slopes)[0]
        require(get("/api/slopes/" + sid)["slope"]["id"] == sid, "单边坡档案无法追溯")
        filtered = get("/api/risk/screening?" + urlencode({"slope_id": sid}))
        require([r["slope_id"] for r in filtered["assessments"]] == [sid], "单边坡研判过滤不一致")
        evaluation = get("/api/risk/evaluation")
        return {"nodes": len(nodes), "edges": len(edges), "slopes": len(slopes),
                "ready_for_formal_risk": completeness["summary"].get("ready"),
                "risk_metrics_available": evaluation.get("metrics") is not None,
                "formal_risk_enabled": False}

    def rules():
        library, payload = get("/api/rules/library"), get("/api/risk/rules")
        require(library.get("execution_enabled") == payload.get("execution_enabled"), "规则库与风险接口的启用状态不一致")
        if library.get("publication_status") == "not_generated":
            raise WaitingForData("候选规则库尚未生成")
        return {"rules": len(library.get("rules", [])), "publication_status": library.get("publication_status"),
                "execution_enabled": library.get("execution_enabled", False)}

    def assets():
        catalog, quality = get("/api/multimodal/assets"), get("/api/multimodal/quality")
        assets = catalog.get("assets", [])
        if not assets:
            raise WaitingForData("尚无图片证据目录")
        require(catalog["count"] == len(assets), "图片目录计数不一致")
        require(quality["summary"]["assets"] == len(assets), "图片目录与质量报告计数不一致")
        attached = {n["id"] for n in get("/api/graph")["nodes"] if n.get("props", {}).get("asset_catalog_member")}
        valid = {a["id"] for a in assets if a.get("validation_status") == "valid"}
        require(attached == valid, "有效图片目录与活动图谱不一致")
        samples = {}
        for asset in assets:
            if asset.get("validation_status") == "valid":
                samples.setdefault(asset.get("subtype", "unknown"), asset)
        for asset in samples.values():
            data = get_bytes("/" + asset["href"])
            require(sha256(data).hexdigest() == asset["sha256"], "图片下载与目录校验和不一致")
        return {"catalog_assets": len(assets), "valid_in_graph": len(valid), "download_samples": len(samples),
                "excluded_historical_caches": quality["summary"].get("stale_pdf_evidence_excluded", 0)}

    def monitoring():
        catalog = get("/api/monitoring/catalog")
        series = catalog.get("series", [])
        if not series:
            raise WaitingForData("会昌监测未导入")
        require(catalog.get("risk_eligible") is False, "待核监测错误进入正式风险输入")
        require(all(s.get("risk_eligible") is False for s in series), "待核监测序列错误启用风险计算")
        require(catalog["files"] == len(series) and catalog["observations"] == sum(s["row_count"] for s in series), "监测目录行数不一致")
        replay = get("/api/monitoring/replay")
        days = (date.fromisoformat(replay["end"]) - date.fromisoformat(replay["start"])).days + 1
        require(len(replay["dates"]) == days, "回放日历丢失缺测日")
        require({s["meta"]["id"] for s in replay["series"]} == {s["id"] for s in series}, "回放漏掉已选监测序列")
        for item in replay["series"]:
            require([p["day"] for p in item["points"]] == replay["dates"], "监测曲线与回放日历不对应")
            require(sum(p["count"] for p in item["points"]) == item["record_count"], "回放的日记录计数不一致")
            require(all(p["components"] is None and p["last_record"] is None for p in item["points"] if p["count"] == 0), "无记录日被插入数值")
        for s in series:
            query = {"series_id": s["id"], "start": (s.get("first_time") or "1900-01-01")[:10],
                     "end": (s.get("last_time") or "1900-01-01")[:10], "include_invalid_time": "true", "limit": "1"}
            obs = get("/api/monitoring/observations?" + urlencode(query))
            require(obs["total"] == s["row_count"], "观测库与目录的总行数不一致")
            for row in obs["rows"]:
                require(row["source"]["sha256"] == s["sha256"] and row["source"]["row"] > 0, "观测来源证据丢失")
            data = get_bytes("/api/monitoring/source?" + urlencode({"series_id": s["id"]}))
            require(sha256(data).hexdigest() == s["sha256"], "监测原表下载与导入版本不一致")
        return {"files": len(series), "observations": catalog["observations"], "replay_days": days,
                "batch_id": catalog["batch_id"], "risk_eligible": False}

    def history():
        catalog = get("/api/inspections/history/catalog")
        if not catalog.get("count"):
            raise WaitingForData("历史巡查未导入")
        require(catalog.get("risk_eligible") is False, "历史巡查错误进入近期风险输入")
        payload = get("/api/inspections/history?asset_type=all&include_undated=true&limit=100")
        rows = list(payload["rows"])
        for offset in range(100, payload["total"], 100):
            rows.extend(get(f"/api/inspections/history?asset_type=all&include_undated=true&limit=100&offset={offset}")["rows"])
        require(len(rows) == catalog["count"] == payload["total"], "巡查分页丢行或目录计数不一致")
        require(len({r["id"] for r in rows}) == len(rows), "巡查记录编号重复")
        require(dict(Counter(r["asset_type"] for r in rows)) == catalog["counts_by_type"], "巡查对象分类计数不一致")
        require(sum(r["observed_date"] is None for r in rows) == catalog["invalid_date_rows"], "缺失日期被填补或丢弃")
        require(all(r["risk_eligible"] is False and r["slope_id"] is None and r["route_id"] is None for r in rows), "未核实的巡查被绑定到具体边坡或路线")
        require(sha256(get_bytes("/api/inspections/history/source")).hexdigest() == catalog["source_sha256"], "巡查原表下载版本不一致")
        current = get("/api/inspections")
        require(current["status"] == "reserved_not_connected" and not current["rows"], "历史记录污染近期巡查接口")
        return {"records": len(rows), "counts_by_type": catalog["counts_by_type"], "missing_dates": catalog["invalid_date_rows"],
                "start": catalog["start"], "end": catalog["end"], "risk_eligible": False}

    def baseline():
        payload = get("/api/monitoring/baseline")
        if not payload.get("series"):
            raise WaitingForData("原值离线基线实验尚未生成")
        require(payload.get("risk_eligible") is False and payload.get("deployment_ready") is False, "原值实验误声明风险或部署能力")
        require(payload.get("actual_availability_verified") is False, "未核发布时间的实验误声明在线验证")
        refs = payload["source_references"]
        catalog = {s["id"]: s for s in get("/api/monitoring/catalog")["series"]}
        require(all(catalog.get(s["id"], {}).get("sha256") == s["sha256"] for s in payload["sources"]), "实验来源不对应当前导入")
        count = 0
        for series in payload["series"]:
            for component in series["components"]:
                index = component["component"] - 1
                for row in component["predictions"]:
                    history = [refs[i] for i in row["input_ids"]]
                    target = refs[row["target_id"]]
                    require(len(history) == 7 and all(r["time"] < target["time"] for r in history), "基线输入跨到目标或未来日期")
                    dates = [date.fromisoformat(r["day"]) for r in history] + [date.fromisoformat(row["target_day"])]
                    require(all((b - a).days == 1 for a, b in zip(dates, dates[1:])), "基线窗口跨缺测断档")
                    last, first = history[-1]["components"][index], history[0]["components"][index]
                    require(math.isclose(row["predictions"]["persistence"], last), "保持值预测不一致")
                    require(math.isclose(row["predictions"]["recent_drift"], last + (last - first) / 6, abs_tol=1e-12), "7日变化预测不一致")
                    require(row["actual"] == target["components"][index], "基线实际值不对应原始记录")
                for partition, metrics in component["metrics"].items():
                    scored = [r for r in component["predictions"] if r["split"] == partition]
                    for method, score in metrics["methods"].items():
                        require(score["count"] == len(scored), "两种基线未对相同目标计分")
                        if scored:
                            mae = sum(abs(r["predictions"][method] - r["actual"]) for r in scored) / len(scored)
                            require(math.isclose(mae, score["mae"], abs_tol=1e-12), "基线误差汇总不一致")
                count += len(component["predictions"])
        return {"sources": len(payload["sources"]), "component_forecasts": count, "deployment_ready": False, "risk_eligible": False}

    def report(kind, endpoint, marker):
        if post_json is None:
            raise WaitingForData("未开启导出核查；使用 --exports 会生成独立测试报告")
        if kind == "监测" and not get("/api/monitoring/catalog").get("series"):
            raise WaitingForData("监测未导入，不能核查离线监测报告")
        if kind != "监测" and not get("/api/graph").get("nodes"):
            raise WaitingForData("图谱未生成，不能核查离线报告")
        result = post_json(endpoint, {"file_name": "流程核查_" + kind + "_" + uuid4().hex[:12]})
        raw = get_bytes(result["download_url"])
        require(len(raw) == result["bytes"], "导出文件大小与接口不一致")
        html = raw.decode("utf-8")
        if marker == "monitoring":
            require("window.MONITORING_OFFLINE=" in html, "监测报告缺少离线数据")
            require("src=\"./monitoring.js\"" not in html, "监测报告仍依赖外部脚本")
        else:
            match = re.search(fr'<script id="{marker}" type="application/json">([\s\S]*?)</script>', html)
            require(match is not None, "报告缺少嵌入数据")
            embedded = json.loads(match.group(1))
            if kind == "图谱":
                require({n["id"] for n in embedded["graph"]["nodes"]} == {n["id"] for n in get("/api/graph")["nodes"]}, "离线图谱与在线版本不一致")
            else:
                require(embedded["risk"]["formal_risk_assessment_enabled"] is False, "离线报告误开启正式风险")
                require({r["slope_id"] for r in embedded["risk"]["assessments"]} == {r["slope_id"] for r in get("/api/risk/screening")["assessments"]}, "离线研判与在线边坡不一致")
        info = {"kind": kind, "file_name": result["file_name"], "download_url": result["download_url"], "bytes": len(raw), "sha256": sha256(raw).hexdigest()}
        exports.append(info)
        return info

    check("documents", "PDF目录到活动图谱版本", documents)
    check("graph_risk", "图谱、档案、完整性和研判一致性", graph_and_risk)
    check("rules", "规则发布与执行状态", rules)
    check("assets", "页面证据目录与图谱", assets)
    check("monitoring", "会昌回放、原值查询与八表来源", monitoring)
    check("history", "历史巡查检索与近期接口隔离", history)
    check("baseline", "原值实验来源、时间窗口和公平计分", baseline)
    for kind, endpoint, marker in [("图谱", "/api/export/standalone", "slopekg-data"),
                                   ("研判", "/api/export/risk-standalone", "slopekg-risk-data"),
                                   ("监测", "/api/export/monitoring", "monitoring")]:
        check("export_" + marker, kind + "离线报告", lambda k=kind, e=endpoint, m=marker: report(k, e, m))

    def unchanged():
        paths = [p for p in ("/api/documents", "/api/graph", "/api/rules/library", "/api/monitoring/catalog", "/api/inspections/history/catalog", "/api/monitoring/baseline") if p in cache]
        require(paths, "没有取得可核对版本的输入")
        require(all(fingerprint(get_json(p)) == fingerprint(cache[p]) for p in paths), "检查期间数据版本发生变化，请待更新完成后重跑")
        return {"input_sha256": {p: fingerprint(cache[p]) for p in paths}}

    check("unchanged", "检查期间输入版本不变", unchanged)
    counts = dict(Counter(c["status"] for c in checks))
    return {"schema_version": "workflow-smoke-1", "checked_at": datetime.now(timezone.utc).isoformat(),
            "status": "failed" if counts.get("failed") else ("partial" if counts.get("waiting") else "passed"),
            "counts": counts, "checks": checks, "exports": exports,
            "boundary": "验证当前已发布结果的功能一致性；不重新解析，不启动OCR或模型，不验证语义准确率，不构成工程风险验收。"}
