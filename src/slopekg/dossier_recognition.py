"""Link saved recognition evidence and adopt reviewed static archive fields."""

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import re
from urllib.parse import urlencode

from .identity import STATION_RE, stable_id
from .manual_contract import FIELD_SPECS
from .manual_validation import normalize_field_value
from .sections import station_bounds
from .storage import read_json, verify_file_sha256, write_json


ALIASES = {
    "slope_height_m": ("坡高", "边坡高度", "坡体高度"),
    "slope_gradient_deg": ("坡度", "边坡坡度", "坡角", "边坡坡角", "坡面倾角"),
    "slope_length_m": ("坡长", "边坡长度"),
    "slope_aspect_deg": ("坡向", "边坡坡向", "坡面倾向"),
    "slope_type": ("边坡类型", "路堤/路堑"),
    "material_nature": ("边坡材料类型", "岩土类型", "边坡性质"),
}
FIELDS = {alias: code for code, aliases in ALIASES.items() for alias in (code, *aliases)}
BOUNDS = {
    "slope_height_m": ("slope_height_min_m", "slope_height_max_m", "slope_height_raw"),
    "slope_gradient_deg": ("slope_gradient_min_deg", "slope_gradient_max_deg", "slope_gradient_raw"),
}
NUMBER = r"[0-9]+(?:\.[0-9]+)?"
RANGE = re.compile(
    rf"\s*(约(?:为)?\s*)?({NUMBER})\s*(m|米|°|度)?\s*(?:[~～—–至-]\s*({NUMBER})\s*(m|米|°|度)?)?\s*"
)


class DossierConflict(ValueError):
    pass


def _path(paths):
    return paths.manual_dir / "dossier_recognition.json"


def records(paths):
    return read_json(_path(paths), {})


def _scope(props):
    return [props.get(key) for key in ("route_code_cache", "start_station_m", "end_station_m", "side")]


def overlay(props, record):
    if record and record["scope"] == _scope(props):
        for entry in record["adopted"].values():
            props.update(entry["patch"])


def _business_document(paths, document):
    from .recognition_debug import DENIED_HASHES, _safe_location

    digest = document.get("source_sha256")
    source = _safe_location(document.get("path") or "", paths.root)
    return (source if source.is_relative_to(paths.raw_dir.resolve()) and source.suffix.lower() == ".pdf"
            and isinstance(digest, str) and len(digest) == 64 and digest not in DENIED_HASHES else None)


def _objects(document):
    object_ids = {row.get("object") for row in document.get("facts", [])}
    for obj in document.get("objects", []):
        kind = obj.get("type") or ""
        if (isinstance(kind, str) and obj.get("id") in object_ids and obj.get("document_id") == document["id"]
                and re.search(r"边坡|滑坡|危岩|崩塌", kind)
                and not re.search(r"部位|分区|局部|断面|剖面|照片|表格|监测|工程|隐患点", kind)):
            yield obj


def available_runs(paths, run_id=""):
    from .agent_service import agent_runs, select_run

    run_ids = [run_id] if run_id else [item["run_id"] for item in agent_runs(paths)["runs"]
                                       if item["processing_status"] in {"completed", "partial"}]
    result = []
    for id_ in run_ids:
        run = select_run(paths, id_)
        if run.get("processing_status") not in {"completed", "partial"} or run.get("test_only"):
            continue
        documents = []
        for doc in run["documents"]:
            try:
                business = _business_document(paths, doc)
            except ValueError:
                continue
            if not business:
                continue
            objects = list(_objects(doc))
            if objects:
                documents.append({"id": doc["id"], "name": doc["name"], "objects": [
                    {"id": obj["id"], "name": obj.get("name") or obj["id"]} for obj in objects
                ]})
        if documents:
            result.append({"run_id": id_, "status": run["processing_status"], "documents": documents})
    return {"runs": result}


def _check_target(props, obj, doc):
    route, start, end, side = _scope(props)
    title = obj.get("name") or ""
    routes = set(re.findall(r"(?<![A-Z0-9])[GSXY]\d{2,4}(?!\d)", (doc["name"] + " " + title).upper()))
    stations = {station_bounds(match[0]) for match in STATION_RE.finditer(title.upper().replace("–", "-").replace("—", "-"))}
    sides = set(re.findall(r"[左右]侧", title))
    if route and len(routes) == 1 and route.upper() not in routes:
        raise ValueError("识别资料的路线与当前档案不一致")
    if start is not None and end is not None and stations and stations != {(start, end)}:
        raise ValueError("识别对象的起止桩号与当前档案不一致")
    if side in {"左侧", "右侧"} and sides and sides != {side}:
        raise ValueError("识别对象的左右侧与当前档案不一致")


def _field_patch(fact, evidence):
    code = FIELDS.get(str(fact.get("field", "")).strip())
    if (not code or not evidence or fact.get("role") != "observed"
            or fact.get("model_status", fact.get("status")) != "ok"):
        return None, None
    spec = FIELD_SPECS[code]
    value = fact.get("value")
    if spec["type"] == "enum":
        return (code, {code: value}) if value in spec["options"] else (None, None)
    match = RANGE.fullmatch(str(value))
    if not match or (match[4] and code not in BOUNDS):
        return None, None
    unit = "°" if code.endswith("_deg") else "m"
    units = {"m", "米"} if unit == "m" else {"°", "度"}
    declared = {item for item in (str(fact.get("unit") or "").strip(), match[3], match[5]) if item}
    if not declared or not declared <= units:
        return None, None
    try:
        low = normalize_field_value(spec, match[2])
        high = normalize_field_value(spec, match[4]) if match[4] else low
    except ValueError:
        return None, None
    if low > high:
        return None, None
    patch = {code: None if match[4] else low}
    if code in BOUNDS:
        lower, upper, raw = BOUNDS[code]
        approx = bool(match[1]) or any(re.search(rf"约(?:为)?\s*{re.escape(match[2])}(?![.\d])", e["quote"]) for e in evidence)
        text = f"{low:g}～{high:g}" if match[4] else f"{low:g}"
        patch.update({lower: low, upper: high, raw: ("约" if approx else "") + text + unit})
    return code, patch


def _snapshot(paths, request, slope):
    from .agent_service import agent_artifact, run_root, select_run

    run_id = request.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("请选择已保存的智能识别任务")
    run = select_run(paths, run_id)
    if run.get("processing_status") not in {"completed", "partial"} or run.get("test_only"):
        raise ValueError("请选择有已保存结果的业务识别任务")
    doc = next((doc for doc in run["documents"] if doc["id"] == request.get("document_id")), None)
    if not doc or not (pdf := _business_document(paths, doc)):
        raise ValueError("只允许关联业务资料库中的开放原件")
    digest = doc["source_sha256"]
    verify_file_sha256(pdf, digest)
    obj = next((obj for obj in _objects(doc) if obj["id"] == request.get("object_id")), None)
    if not obj:
        raise ValueError("请选择资料中的边坡或滑坡对象")
    _check_target(slope["props"], obj, doc)
    root = run_root(paths, run)
    manifest = read_json(root / "workspace/manifest.json", {})
    item = next((item for item in manifest.get("documents", [])
                 if item["document_id"] == doc["id"] and item["original_source_sha256"] == digest), None)
    if not item:
        raise ValueError("识别任务缺少与原件匹配的页清单")
    pages = {}
    for row in item["files"]:
        if row["kind"] == "source_page_image" and row["physical_page"] in item["selected_physical_pages"]:
            path = "workspace/" + row["path"]
            verify_file_sha256(agent_artifact(paths, path, run_id), row["sha256"])
            pages[row["physical_page"]] = (path, row["sha256"])
    facts = {}
    for fact in doc["facts"]:
        if fact.get("object") != obj["id"] or fact.get("document_id") != doc["id"]:
            continue
        evidence = []
        for ref in fact["evidence"] if isinstance(fact.get("evidence"), list) else [fact.get("evidence")]:
            if isinstance(ref, dict) and isinstance(ref.get("quote"), str) and ref["quote"].strip() and ref.get("physical_page") in pages:
                page = ref["physical_page"]
                path, checksum = pages[page]
                evidence.append({"page": page, "quote": ref["quote"], "locator": ref.get("locator"),
                                 "path": path, "sha256": checksum, "url": "/api/agent-recognition/artifact?" +
                                 urlencode({"run": run_id, "path": path})})
        code, patch = _field_patch(fact, evidence)
        if fact["id"] in facts:
            raise ValueError("识别结果有重复的候选编号")
        facts[fact["id"]] = {"id": fact["id"], "label": fact.get("field"), "value": fact.get("value"),
                             "unit": fact.get("unit"), "condition": fact.get("condition"), "evidence": evidence,
                             "field": code, "patch": patch}
    key = stable_id("source", f"{run_id}:{doc['id']}:{obj['id']}:{digest}")
    return {"id": key, "run_id": run_id, "document_id": doc["id"], "document": doc["name"],
            "object": obj.get("name") or obj["id"], "path": str(pdf), "sha256": digest, "facts": facts}


def _actor(request):
    actor = request.get("actor")
    if not isinstance(actor, str) or not actor.strip() or len(actor) > 100:
        raise ValueError("请填写核对人姓名")
    return actor.strip()


def _find(paths, slope_id):
    from .queries import find_slope

    if not isinstance(slope_id, str) or not (slope := find_slope(slope_id, paths)):
        raise KeyError("未找到该边坡档案")
    return slope


def _entry(data, slope):
    entry = data.get(slope["id"])
    if entry and entry["scope"] != _scope(slope["props"]):
        raise DossierConflict("档案的路线、范围或侧别已变化，请先核对既有关联")
    return entry or {"scope": _scope(slope["props"]), "sources": {}, "adopted": {}, "history": []}


def _values(props, code):
    return {key: props.get(key) for key in (code, *BOUNDS.get(code, ()))}


def _view(slope, entry):
    from .standard_assessment.contract import identity

    props = slope["props"]
    current = {code: _values(props, code) for code in ALIASES}
    manual = props.get("manual_override", {}).get("fields", [])
    return {"sources": list(entry["sources"].values()), "adopted": entry["adopted"], "history": entry["history"], "current": current,
            "manual_fields": manual, "revision": identity("dossier_", [entry, current, manual])}


def dossier(paths, slope_id):
    slope = _find(paths, slope_id)
    return _view(slope, _entry(records(paths), slope))


def link(paths, request):
    from .manual import _WRITE_LOCK

    actor = _actor(request)
    if request.get("same_object_confirmed") is not True:
        raise ValueError("请先确认识别对象与档案为同一个边坡")
    slope = _find(paths, request.get("slope_id"))
    source = _snapshot(paths, request, slope)
    scope = _scope(slope["props"])
    with _WRITE_LOCK:
        slope = _find(paths, slope["id"])
        if _scope(slope["props"]) != scope:
            raise DossierConflict("档案范围已更新，请重新打开核对")
        data = records(paths)
        entry = _entry(data, slope)
        if any(id_ != slope["id"] and source["id"] in record["sources"] for id_, record in data.items()):
            raise DossierConflict("该识别对象已关联另一处边坡")
        if source["id"] not in entry["sources"]:
            source.update(linked_by=actor, linked_at=datetime.now(timezone.utc).isoformat())
            entry["sources"][source["id"]] = source
            data[slope["id"]] = entry
            write_json(_path(paths), data)
        return _view(slope, entry)


def adopt(paths, request):
    from .agent_service import agent_artifact
    from .manual import _WRITE_LOCK

    actor = _actor(request)
    reason = request.get("reason")
    ids = request.get("fact_ids")
    if request.get("source_reviewed") is not True or not isinstance(reason, str) or not 0 < len(reason.strip()) <= 500:
        raise ValueError("请核对原页并填写采用依据")
    if not isinstance(ids, list) or not 0 < len(ids) <= len(ALIASES) or not all(isinstance(id_, str) for id_ in ids):
        raise ValueError("请选择要写入的字段")
    with _WRITE_LOCK:
        slope = _find(paths, request.get("slope_id"))
        data = records(paths)
        entry = _entry(data, slope)
        before = _view(slope, entry)
        if request.get("revision") != before["revision"]:
            raise DossierConflict("档案已有更新，请重新打开核对")
        source = entry["sources"].get(str(request.get("source_id")))
        if not source:
            raise KeyError("所选识别资料尚未关联到本边坡")
        facts = [source["facts"].get(id_) for id_ in ids]
        if any(not fact or not fact["patch"] for fact in facts):
            raise ValueError("只可采用有原页证据且格式受支持的字段")
        if len({fact["field"] for fact in facts}) != len(facts):
            raise ValueError("同一档案字段只能选一个候选值")
        verify_file_sha256(Path(source["path"]), source["sha256"])
        changes = []
        for fact in facts:
            code = fact["field"]
            if code in before["manual_fields"]:
                raise DossierConflict("所选字段已有人工补录值，请在人工表单核对")
            for ref in fact["evidence"]:
                verify_file_sha256(agent_artifact(paths, ref["path"], source["run_id"]), ref["sha256"])
            old = deepcopy(entry["adopted"].get(code))
            new = {"source_id": source["id"], "fact_id": fact["id"], "patch": fact["patch"]}
            if old == new:
                continue
            changes.append({"field": code, "previous": before["current"][code], "previous_adoption": old, "adopted": new})
            entry["adopted"][code] = new
        if changes:
            entry["history"].append({"actor": actor, "reason": reason.strip(), "time": datetime.now(timezone.utc).isoformat(),
                                     "changes": changes})
            data[slope["id"]] = entry
            write_json(_path(paths), data)
        return {"updated": len(changes)}
