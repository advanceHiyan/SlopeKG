"""Evidence contracts shared by visual readers, reviewers and consumers."""
from __future__ import annotations

import math
from typing import Any

from .model_client import content_hash
from .visual_evidence import field_parts_error

VERSION = "visual-v11"
ROLES = {"observation", "design", "recommendation", "calculation", "prediction", "simulation", "reference", "unknown"}
REVIEW_CHECKS = ("object_supported","field_supported","value_supported","unit_supported","scope_supported",
                 "role_supported","qualifiers_supported","condition_supported","time_supported")


class ContractError(ValueError):
    pass


def bbox(value: Any) -> list[float]:
    if (not isinstance(value, list) or len(value) != 4
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value)):
        raise ContractError("bbox must contain four finite normalized coordinates")
    x0, y0, x1, y1 = value
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ContractError("bbox outside normalized page")
    return list(value)


def text(value: Any, name: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ContractError(f"{name} must be a string")
    return value


def rows(value: Any, name: str) -> list[dict]:
    if not isinstance(value, list) or any(not isinstance(v, dict) for v in value):
        raise ContractError(f"{name} must be a list of objects")
    return value


def review_result(answer: dict, expected: set[str]) -> dict:
    """Quarantine one malformed verdict without discarding other reviewed facts."""
    raw = rows(answer.get("verdicts"), "verdicts")
    groups: dict[str,list[dict]] = {}
    for verdict in raw:
        if isinstance(verdict.get("id"),str): groups.setdefault(verdict["id"],[]).append(verdict)
    result=[]
    for identifier in sorted(expected):
        matches=groups.get(identifier,[])
        errors=[]
        verdict=dict(matches[0]) if len(matches)==1 else {"id":identifier,"decision":"uncertain","quote":"","reason":"missing or duplicated verdict"}
        if len(matches)!=1: errors.append("missing_or_duplicated_verdict")
        if verdict.get("decision") not in {"supported","uncertain","rejected"}:
            errors.append("decision"); verdict["decision"]="uncertain"
        # Normalize one known spelling alias without inventing a semantic answer.
        alias = verdict.get('qualifier_supported')
        if 'qualifiers_supported' not in verdict and type(alias) is bool:
            verdict['qualifiers_supported'] = alias
            verdict.setdefault('contract_repairs',[]).append('qualifier_supported -> qualifiers_supported')
        elif 'qualifier_supported' in verdict and alias != verdict.get('qualifiers_supported'):
            verdict['qualifiers_supported'] = None
        for key in REVIEW_CHECKS:
            if type(verdict.get(key)) is not bool:
                errors.append(key);verdict[key]=False
        for key in ("quote","reason"):
            if not isinstance(verdict.get(key),str): errors.append(key);verdict[key]=""
        fragments=verdict.get("support_quotes",[])
        if not isinstance(fragments,list) or any(not isinstance(q,str) or not q.strip() for q in fragments):
            errors.append("support_quotes"); verdict["support_quotes"]=[]
            verdict["decision"]="uncertain"
        if errors:
            verdict.update(validation_errors=errors,raw_review=matches)
        result.append(verdict)
    return {**answer,"verdicts":result,"unmatched_verdicts":[v for v in raw if v.get("id") not in expected]}


def page_card(answer: dict, page: int, anchors: list[dict] | None = None, padding: float = 0) -> dict:
    regions = rows(answer.get("regions"), "regions")
    by_anchor = {a["id"]: a for a in anchors or []}
    seen = set()
    for region in regions:
        rid = text(region.get("id"), "region id")
        if rid in seen:
            raise ContractError("Duplicate region id")
        seen.add(rid)
        bbox(region.get("bbox"))
        region.setdefault("model_bbox", list(region["bbox"]))
        region["bbox"] = list(region["model_bbox"])
        text(region.get("kind"), "region kind")
        text(region.get("summary"), "region summary", empty=True)
        if not isinstance(region.get("objects"), list) or any(not isinstance(v, str) for v in region["objects"]):
            raise ContractError("Region objects must be a string list")
        anchor_ids = region.get("anchor_ids", [])
        if not isinstance(anchor_ids, list) or any(a not in by_anchor for a in anchor_ids):
            raise ContractError("Layout cites unknown geometric anchors")
        if anchor_ids:
            boxes = [by_anchor[a]["bbox"] for a in anchor_ids]
            if region["kind"] != "text":
                boxes.append(region["bbox"])  # Preserve non-text borders/graphics and empty cells.
            region["proposed_bbox"] = region["bbox"]
            region["bbox"] = [min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes)]
            region["geometry_source"] = "model_grouped_source_anchors"
        else:
            region["geometry_source"] = "model_approximation"
        if not isinstance(region.get("table_id",""),str):
            raise ContractError("table_id must be a model-assigned string")
    # The model identifies shared table semantics; geometry only combines its
    # explicit groups. Never detach data cells from their headers and units.
    merged=[]
    tables={}
    for region in regions:
        table_id=region.get("table_id","") if region["kind"]=="table" else ""
        if table_id and table_id in tables:
            target=tables[table_id]
            for key in ("bbox","model_bbox"):
                a,b=target[key],region[key]
                target[key]=[min(a[0],b[0]),min(a[1],b[1]),max(a[2],b[2]),max(a[3],b[3])]
            target["anchor_ids"]=list(dict.fromkeys(target.get("anchor_ids",[])+region.get("anchor_ids",[])))
            target["source_region_ids"]+=region.get("source_region_ids",[region["id"]])
            target["objects"]=list(dict.fromkeys(target["objects"]+region["objects"]))
            target["summary"]+="；"+region["summary"]
        else:
            region.setdefault("source_region_ids",[region["id"]])
            merged.append(region)
            if table_id:tables[table_id]=region
    regions=merged
    if padding:
        for region in regions:
            b=region["bbox"]
            region["bbox"]=[max(0,b[0]-padding),max(0,b[1]-padding),min(1,b[2]+padding),min(1,b[3]+padding)]
    for key in ("title", "summary"):
        text(answer.get(key), key, empty=True)
    if not isinstance(answer.get("blank"), bool):
        raise ContractError("Page blank state missing")
    # Even a model-classified blank page is audited against the complete image.
    assigned = {a for r in regions for a in r.get("anchor_ids", [])}
    return {**answer, "page": page, "regions": regions,
            "unassigned_anchor_ids": sorted(set(by_anchor) - assigned)}


FACT_KEYS = ("object", "field", "value", "unit", "qualifier", "scope", "role", "condition", "time")


def validate_fact(fact: dict) -> dict:
    fact.setdefault('unit_basis','')
    text(fact['unit_basis'], 'unit_basis', empty=True)
    for key in FACT_KEYS:
        text(fact.get(key), key, empty=key in {"unit", "qualifier", "condition", "time"})
    if fact["role"] not in ROLES:
        raise ContractError("Unknown fact role")
    text(fact.get("quote"), "quote")
    fragments=fact.get("support_quotes",[])
    if not isinstance(fragments,list) or any(not isinstance(q,str) or not q.strip() for q in fragments):
        raise ContractError("support_quotes must be a list of nonempty source fragments")
    bbox(fact.get("bbox"))
    if error := field_parts_error(fact):
        # A malformed optional proof quarantines this assertion, not the
        # transcript and all other facts returned by the same paid reading.
        fact["evidence_contract_errors"]=[error]
    return fact


def reading(answer: dict) -> dict:
    text(answer.get("transcript"), "transcript", empty=True)
    facts = rows(answer.get("facts"), "facts")
    for fact in facts:
        validate_fact(fact)
    for table in rows(answer.get("tables"), "tables"):
        bbox(table.get("bbox"))
        text(table.get("caption"), "table caption", empty=True)
        cells = rows(table.get("cells"), "cells")
        for cell in cells:
            for k in ("row", "column", "row_span", "column_span"):
                n = cell.get(k)
                if type(n) is not int or n < (1 if k.endswith("span") else 0):
                    raise ContractError("Invalid table cell position/span")
            text(cell.get("text"), "cell text", empty=True)
            text(cell.get("header"), "cell header", empty=True)
            validate_content_links(cell, len(facts))
    for dimension in rows(answer.get("dimensions"), "dimensions"):
        text(dimension.get("text"), "dimension text")
        bbox(dimension.get("bbox"))
        validate_content_links(dimension, len(facts))
    for issue in rows(answer.get("uncertainties"), "uncertainties"):
        bbox(issue.get("bbox"))
        text(issue.get("reason"), "uncertainty reason")
        if issue.get("rotation", 0) not in (0, 90, 180, 270):
            raise ContractError("Invalid reread rotation")
        if issue.get("kind","unspecified") not in {"unspecified","crop_boundary","unreadable","binding","background"}:
            raise ContractError("Invalid uncertainty kind")
        if (not isinstance(issue.get("edges",[]),list) or
                any(e not in {"top","bottom","left","right"} for e in issue.get("edges",[]))):
            raise ContractError("Invalid uncertainty edges")
        if "fact_indices" in issue and (not isinstance(issue["fact_indices"],list) or
                any(type(i) is not int or not 0<=i<len(facts) for i in issue["fact_indices"])):
            raise ContractError("Uncertainty cites unknown fact index")
    return answer


def validate_content_links(item: dict, fact_count: int) -> None:
    indices=item.get('fact_indices',[])
    if (not isinstance(indices,list) or any(type(i) is not int or not 0 <= i < fact_count for i in indices)
            or len(set(indices)) != len(indices)):
        raise ContractError('Content cites unknown or duplicated fact index')
    disposition=item.get('disposition','unresolved')
    if disposition not in {'fact','reference_only','unresolved'}:
        raise ContractError('Unknown source content disposition')
    if disposition=='reference_only' and (not isinstance(item.get('reason'),str) or not item['reason'].strip()):
        raise ContractError('Unextracted source content needs a reason')
    if disposition=='fact' and not indices:
        raise ContractError('Fact disposition needs an explicit fact link')


def inside(inner: list, outer: list, tolerance: float = .002) -> bool:
    return all((inner[i] >= outer[i] - tolerance if i < 2 else inner[i] <= outer[i] + tolerance)
               for i in range(4))


def fact_key(fact: dict) -> str:
    return content_hash({key: fact.get(key) for key in (*FACT_KEYS,'unit_basis')})


def resolve_conflicts(facts: list[dict]) -> None:
    """Preserve alternatives. Different scope/role/condition/time are not conflicts."""
    groups: dict[str, list[dict]] = {}
    for fact in facts:
        key = content_hash({**{k: fact[k] for k in ("document_id", "object", "field", "scope", "role", "condition", "time")},
                            "section_scope":fact.get("section_scope",f"page:{fact.get('page')}")})
        groups.setdefault(key, []).append(fact)
    for group in groups.values():
        if len({(f["value"], f["unit"], f.get("unit_basis", ""), f["qualifier"])
                for f in group if f["status"] == "verified_model"}) > 1:
            for fact in group:
                if fact["status"] == "verified_model":
                    fact.update(status="pending", reason="conflicting_source_values", conflict_id=content_hash([f["id"] for f in group]))


def consolidate(facts: list[dict]) -> list[dict]:
    """Deduplicate identical assertions, retaining every read/review as evidence."""
    groups: dict[str, list[dict]] = {}
    for fact in facts:
        groups.setdefault(content_hash([fact["document_id"], fact_key(fact)]), []).append(fact)
    result = []
    priority = {"verified_model": 2, "pending": 1, "rejected": 0}
    for key, group in groups.items():
        clusters: list[list[dict]] = []
        for fact in group:
            cluster = next((g for g in clusters if any(
                (fact.get("section_scope") and fact.get("section_scope") == other.get("section_scope")) or
                (fact["page"] == other["page"] and
                 fact["bbox"][0] < other["bbox"][2] and fact["bbox"][2] > other["bbox"][0] and
                 fact["bbox"][1] < other["bbox"][3] and fact["bbox"][3] > other["bbox"][1]) for other in g)),None)
            if cluster is None: clusters.append([fact])
            else: cluster.append(fact)
        for group_index, cluster in enumerate(clusters):
            best = max(cluster, key=lambda f: priority[f["status"]])
            row = {**best, "id": content_hash([key,group_index])[:24], "evidence": [dict(read_id=f["id"], page=f["page"],
                        region_id=f["region_id"], bbox=f["bbox"], quote=f["quote"], support_quotes=f.get("support_quotes",[]),
                        field_evidence=f.get("field_evidence",{}), source_grounding=f.get("source_grounding",[]),
                        admission_checks=f.get("admission_checks",{}),
                        review_history=f.get("review_history",[]),
                        support_context=f.get("support_context",[]),
                        section_scope=f.get("section_scope"),
                        status=f["status"], reason=f["reason"], review=f.get("review")) for f in cluster]}
            if any(f["status"] == "rejected" for f in cluster) and row["status"] == "verified_model":
                row.update(status="pending", reason="review_disagreement")
            result.append(row)
    return result


# Compatibility exports; validation and prompt text have separate responsibilities.
from .visual_prompts import SYSTEM, LAYOUT, SEGMENT, READ, VERIFY, AUDIT, ROLE_GUIDANCE
