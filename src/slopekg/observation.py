"""Clause-local polarity for historical document observations."""
from __future__ import annotations

import re

NEGATION = re.compile(r"未(?:见|发现|发生|出现)|尚未|没有|不存在|无(?:明显)?(?:地)?")
PREDICTION = re.compile(r"可能|有可能|易(?:于)?(?:发生|产生|形成|出现)|极易|将(?:会)?|建议|应当|宜|如不|若不|一旦|通过计算|通过模拟|模拟结果|计算选取|公式|式中")


def observation_clauses(text: str) -> list[str]:
    """Keep positive clauses even when a neighbouring clause is negative/future."""
    result = []
    for sentence in re.split(r"[。；;]", text):
        if re.search(r"通过(?:计算|模拟)|模拟结果|计算选取|公式|式中", sentence):
            continue
        for clause in re.split(r"[，,]|但是|但|然而", sentence):
            clause = re.sub(r"\s+", "", clause)
            if clause:
                result.append(clause)
    return result


def positive_mention(clause: str, term: str) -> bool:
    for match in re.finditer(re.escape(term), clause):
        prefix = clause[:match.start()]
        if not NEGATION.search(prefix) and not PREDICTION.search(prefix):
            # Postposed negation: “裂缝未见发育” is not an observation either.
            if not re.match(r"(?:未见|未发生|不存在|不发育)", clause[match.end():]):
                return True
    return False


def supported_observation(item: dict, source: str, *, domain: str) -> bool:
    description = re.sub(r"\s+", "", str(item.get("description") or ""))
    if len(description) < 4 or description not in re.sub(r"\s+", "", source):
        return False
    terms = (["裂缝", "垮塌", "掉块", "掉落", "滑塌", "溜滑", "崩塌", "落石", "变形", "隆起", "管涌", "渗水"]
             if domain == "deformation" else ["地下水", "地表水", "汇水", "渗水", "滴水", "冲刷", "排水"])
    relevant = [term for term in terms if term in str(item.get("type") or "")]
    return bool(relevant) and any(positive_mention(clause, term)
                                 for clause in observation_clauses(description) for term in relevant)


def belongs_to_station(item: dict, station: str) -> bool:
    from .extractors import STATION_RE, normalize_station_match
    mentions = {normalize_station_match(m) for m in STATION_RE.finditer(str(item.get("description") or ""))}
    return not mentions or station in mentions
