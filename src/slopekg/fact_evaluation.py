"""Offline, input-dependent checks of explicitly scoped graph facts.

This is a regression benchmark, not a closed-world annotation of every node.
Unannotated facts are therefore not false positives. Gold data never enters
the extraction pipeline. Ranges and orientation pairs are matched together.
"""
from __future__ import annotations

import math
import re
from typing import Any


def equivalent(actual: Any, expected: Any, *, tolerance: float = 1e-6) -> bool:
    if expected is None or isinstance(expected, bool):
        return actual is expected
    if isinstance(expected, (int, float)):
        return (isinstance(actual, (int, float)) and not isinstance(actual, bool)
                and math.isfinite(actual) and math.isclose(actual, expected, rel_tol=0, abs_tol=tolerance))
    if isinstance(expected, str):
        return isinstance(actual, str) and re.sub(r"\s+", "", actual) == re.sub(r"\s+", "", expected)
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            equivalent(a, e, tolerance=tolerance) for a, e in zip(actual, expected))
    return actual == expected


def field_matches(props: dict, field: str, value: Any, fact: dict) -> bool:
    aliases = fact.get("aliases", {}).get(field, {})
    actual = props.get(field)
    if isinstance(actual, str):
        actual = aliases.get(actual, actual)
    return equivalent(actual, value, tolerance=fact.get("tolerance", 1e-6))


def evaluate_graph_facts(graph: dict, gold: dict) -> dict:
    nodes = {n["id"]: n for n in graph["nodes"]}
    slopes = [n for n in nodes.values() if n["type"] == "Slope"]
    results = []
    ids = set()
    for fact in gold["facts"]:
        if fact["id"] in ids:
            raise ValueError(f"Duplicate fact id: {fact['id']}")
        ids.add(fact["id"])
        owners = [s for s in slopes if s["props"].get("route_code_cache") == fact["route"]
                  and f"{s['props'].get('start_station_raw')}-{s['props'].get('end_station_raw')}" == fact["station"]]
        candidates = []
        if len(owners) == 1:
            if fact["node_type"] == "Slope":
                candidates = owners
            else:
                targets = {e["target"] for e in graph["edges"] if e["source"] == owners[0]["id"]
                           and e["relation"] == fact["relation"]}
                candidates = [nodes[t] for t in targets if t in nodes and nodes[t]["type"] == fact["node_type"]]
        candidates = [n for n in candidates if all(field_matches(n["props"], k, v, fact)
                      for k, v in fact.get("where", {}).items())]
        if fact.get("description_contains"):
            candidates = [n for n in candidates if fact["description_contains"] in
                          re.sub(r"\s+", "", str(n["props"].get("description", "")))]
        matching = [n for n in candidates if all(field_matches(n["props"], k, v, fact)
                    for k, v in fact["expected"].items())]
        if len(owners) != 1:
            status = "missing_owner" if not owners else "ambiguous_owner"
        elif fact.get("expect_absent"):
            status = "violation" if matching else "pass"
        elif matching:
            status = "conflict" if len(matching) != len(candidates) and not fact.get("collection_member") else "pass"
        else:
            has_value = any(any(n["props"].get(k) not in (None, "", []) for k in fact["expected"])
                            for n in candidates)
            status = "wrong" if has_value else "missing"
        results.append({"id": fact["id"], "status": status, "expected": fact["expected"],
                        "source": fact.get("source"), "route": fact["route"], "station": fact["station"],
                        "matching_nodes": [n["id"] for n in matching],
                        "candidate_nodes": [{"id": n["id"], "values": {k: n["props"].get(k)
                                             for k in fact["expected"]}} for n in candidates],
                        "duplicate_matches": max(0, len(matching) - 1) if not fact.get("expect_absent") else 0})
    counts = {status: sum(r["status"] == status for r in results) for status in
              ("pass", "wrong", "missing", "conflict", "violation", "missing_owner", "ambiguous_owner")}
    return {"benchmark_version": gold["version"], "scope": gold["scope"],
            "review_status": gold["review_status"], "facts_total": len(results),
            "facts_passed": counts["pass"], "counts": counts,
            "duplicate_matches": sum(r["duplicate_matches"] for r in results),
            "metric_note": "限定事实回归检查；未标注节点不计FP，不输出全图精确率；原页由AI核对，待独立人工复核。",
            "facts": results}
