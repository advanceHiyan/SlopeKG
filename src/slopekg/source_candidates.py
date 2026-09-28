"""Source-side omission signals. These are candidates, never accuracy labels.

No gold answers, file names or station-specific rules are used here. An
unresolved object stays unresolved instead of being assigned by its number.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any


PAIR = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*[°º]?\s*∠\s*(\d{1,2}(?:\.\d+)?)(?![\d.])\s*[°º]?")
OBJECT = re.compile(r"坡面|坡向|坡角|岩层(?:面|产状)?|层理|层面|地层岩性|地层|(?:主控)?结构面|主控|组合交线|节理|裂隙")


def preceding_section_station(text: str, end: int) -> str | None:
    from .extractors import STATION_RE, normalize_station_match
    headings = [m for m in STATION_RE.finditer(text[:end])
                if re.search(r"\b\d+(?:\.\d+){1,3}\s*$", text[max(0,m.start()-30):m.start()])]
    return normalize_station_match(headings[-1]) if headings else None


def plane_role(name: str) -> str | None:
    if "坡面" in name:
        return "face"
    if any(t in name for t in ("岩层", "层理", "层面", "地层岩性")):
        return "bedding"
    if any(t in name for t in ("结构面", "节理", "裂隙")) or re.fullmatch(r"L\d*|J\d*", name):
        return "joint"
    return None


def orientation_candidates(text: str) -> list[dict[str, Any]]:
    """Find numbers first, then resolve their subject within the same sentence.

    Explicit object switches reset scope. Tables and figure codes are left to
    the existing typed parser. A geological layer subject requires an explicit
    产状 predicate; a material name alone does not establish bedding.
    """
    output = []
    for match in PAIR.finditer(text):
        direction, angle = map(float, match.groups())
        start = max(text.rfind(c, 0, match.start()) for c in "。；;！？\n") + 1
        prefix = text[start:match.start()]
        # PDF line breaks within prose are normalized by callers. Never cross
        # a sentence boundary to borrow an object from unrelated prose.
        names = list(OBJECT.finditer(prefix))
        subject = names[-1] if names else None
        role = plane_role(subject.group()) if subject else None
        if subject and subject.group() == "地层岩性" and "产状" not in prefix[subject.end():]:
            role = None
        if re.search(r"对岸|对面|其他边坡|邻坡|示例|假设", prefix):
            role = None
        ranged = bool(re.search(r"\d\s*[°º]?\s*[-~～—至]\s*$", prefix)
                      or re.match(r"\s*[-~～—至]\s*\d", text[match.end():]))
        if ranged:
            role = None
            reason = "orientation_range_not_scalar"
        elif direction > 360 or angle > 90:
            role = None
            reason = "orientation_out_of_range"
        else:
            reason = "explicit_sentence_subject" if role else "orientation_subject_unresolved"
        output.append(dict(kind="orientation", dip_direction=direction, dip_angle=angle,
                           role=role, reason=reason, start=match.start(), end=match.end(),
                           station_hint=preceding_section_station(text, match.start()),
                           quote=text[start:match.end()]))
    return output


def missing_orientations(samples: list[dict[str, Any]], planes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    known = {(plane_role(str(p.get("name", ""))), p.get("dip_direction"), p.get("dip_angle")) for p in planes}
    missing = []
    for sample in samples:
        for item in orientation_candidates(re.sub(r"\s+", " ", str(sample.get("text", "")))):
            if item['station_hint'] and sample.get('station_context') and item['station_hint'] != sample['station_context']:
                continue
            if item["role"] and (item["role"], item["dip_direction"], item["dip_angle"]) not in known:
                missing.append({**item, "document_id": sample.get("document_id"), "page": sample.get("page")})
    return missing


def material_mentions(text: str) -> list[dict[str, Any]]:
    """Keep cover/fill/sliding-bed roles separate from slope-body mentions."""
    output = []
    for clause in re.split(r"[。；;]", re.sub(r"\s+", "", text)):
        for match in re.finditer(r"碎石土|块石土|[黏粘]土|页岩|灰岩|砂岩|泥岩|白云岩", clause):
            left = clause[:match.start()]
            right = clause[match.end():match.end()+8]
            scopes = list(re.finditer(r"覆盖层|覆盖|坡表|表层|滑床|滑体|坡体|边坡|地层岩性|填充|充填", left))
            scope = scopes[-1].group() if scopes else None
            role = ("joint_fill" if re.match(r"(?:质)?(?:充填|填充)", right) or scope in {"充填", "填充"}
                    else "cover" if scope in {"覆盖层", "覆盖", "坡表", "表层"}
                    else "sliding_bed" if scope == "滑床"
                    else "body" if scope in {"滑体", "坡体", "边坡", "地层岩性"}
                    else "unresolved")
            output.append(dict(kind="material_mention", material=match.group(), role=role,
                               quote=clause, start=match.start(), end=match.end()))
    return output


def body_material_claims(text: str) -> list[dict[str, Any]]:
    """Composition predicates refer to the body, not to co-occurring fill."""
    output = []
    text = re.sub(r"\s+", "", text)
    for match in re.finditer(r"(?:该段)?边坡(?:地层|地质|基岩)?岩性(?:主要)?为([^。；，,]{1,100})|坡体(?:主要)?由([^。；，,]{1,100})", text):
        start = max(text.rfind(c, 0, match.start()) for c in '。；，,') + 1
        if re.search(r"对岸|对面|其他|邻坡|示例|假设", text[start:match.start()]):
            continue
        composition = next(g for g in match.groups() if g is not None)
        soil = bool(re.search(r"碎石土|块石土|[黏粘]土|堆积体", composition))
        rock = bool(re.search(r"页岩|灰岩|砂岩|泥岩|白云岩|板岩", composition))
        value = "土岩混合" if soil and rock else "岩质" if rock else "土质" if soil else None
        if value:
            output.append(dict(value=value, quote=match.group(), role="body",
                               station_hint=preceding_section_station(text, match.start())))
    return output


def build_processing_audit(parsed: dict[str, Any], extracted: dict[str, Any], graph: dict[str, Any]) -> dict[str, Any]:
    """Locate candidate losses, without treating candidate counts as recall.

    Coverage is limited to retained section samples and engineering table
    sidecars. It cannot detect facts absent from PDF parsing/OCR or samples.
    """
    nodes = {n["id"]: n for n in graph["nodes"]}
    children: dict[str, list[dict[str, Any]]] = {}
    for edge in graph["edges"]:
        if edge["target"] in nodes:
            children.setdefault(edge["source"], []).append(nodes[edge["target"]])
    from .graph_builder import stable_id
    rows = []
    for slope in extracted.get("slopes", []):
        target = nodes.get(stable_id("slope", f"{slope.get('route_code')}:{slope['station']}"))
        planes = [n["props"] for n in children.get(target["id"], []) if n["type"] == "StructuralPlane"] if target else []
        known = {(plane_role(str(p.get("name", ""))), p.get("dip_direction"), p.get("dip_angle")) for p in planes}
        basic = {(plane_role(str(p.get("name", ""))), p.get("dip_direction"), p.get("dip_angle")) for p in slope.get("structural_planes", [])}
        for sample in slope.get("section_text_samples", []):
            if sample.get("station_context") not in (None, slope["station"]):
                continue
            text = re.sub(r"\s+", " ", str(sample.get("text", "")))
            for item in [*orientation_candidates(text), *material_mentions(text)]:
                if item["kind"] == "orientation":
                    key = (item["role"], item["dip_direction"], item["dip_angle"])
                    disposition = ("needs_object_review" if not item["role"] else "represented" if key in known
                                   else "lost_after_extraction" if key in basic else "not_extracted")
                    if item['station_hint'] and item['station_hint'] != slope['station']:
                        disposition = 'needs_station_review'
                else:
                    disposition = "scoped_material_evidence"
                rows.append({**item, "disposition": disposition, "route_code": slope.get("route_code"),
                             "station": slope["station"], "document_id": sample.get("document_id"),
                             "page": sample.get("page"), "panel": sample.get("panel")})
    records = {r["id"]: r for r in extracted.get("engineering_records", [])}
    document_kinds = {d["id"]: d.get("kind") for d in parsed.get("documents", [])}
    for table in parsed.get("tables", []):
        if document_kinds.get(table["document_id"]) not in {"勘察报告", "施工图"}:
            continue
        header = " ".join(str(c or "") for r in table.get("rows", [])[:2] for c in r)
        if not re.search(r"稳定性?系数|F[Ss]", header):
            continue
        record = records.get(table["id"], {})
        disposition = ("table_parse_failed" if table.get("review_status") == "failed" else
                       "table_not_extracted" if not record.get("stability_results") else
                       "table_association_unresolved" if not record.get("station") else "table_extracted")
        rows.append(dict(kind="factor_table", source_id=table["id"], document_id=table["document_id"],
                         page=table.get("page"), bbox=table.get("bbox"), station=record.get("station"),
                         disposition=disposition, quote=header, issues=record.get("quality_issues", [])))
    unique = {}
    for row in rows:
        signature = repr(sorted(row.items()))
        row["id"] = "candidate_" + hashlib.sha256(signature.encode()).hexdigest()[:20]
        unique[row["id"]] = row
    result = list(unique.values())
    return {"schema_version": 1, "scope": "retained_section_samples_and_table_sidecars",
            "is_accuracy_measurement": False, "summary": dict(Counter(r["disposition"] for r in result)),
            "candidates": result}
