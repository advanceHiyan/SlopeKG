"""Dependency-free route, station and deterministic ID primitives.

Keep normalization and ID algorithms stable: persisted graph identities depend on them.
"""
from __future__ import annotations
import hashlib
import re
from typing import Any

STATION_RE = re.compile(
    r"K\s*(?P<start_km>\d+)\s*\+\s*(?P<start_m>\d+)\s*[-～~]\s*"
    r"(?:K\s*)?(?:(?P<end_km>\d+)\s*\+)?\s*(?P<end_m>\d+)",
    re.IGNORECASE,
)


ROUTE_CODE_RE = re.compile(r"\b([GSXY]\d{2,4})\b", re.IGNORECASE)


def normalize_station_match(match: re.Match[str] | None) -> str | None:
    if not match:
        return None
    start_km = int(match.group("start_km"))
    start_m = int(match.group("start_m"))
    end_km_raw = match.group("end_km")
    end_km = int(end_km_raw) if end_km_raw else start_km
    end_m = int(match.group("end_m"))
    return f"K{start_km}+{start_m:03d}-K{end_km}+{end_m:03d}"


def infer_route_code_from_values(*values: Any) -> str:
    match = ROUTE_CODE_RE.search(" ".join(str(value or "") for value in values))
    return match.group(1).upper() if match else "ROUTE"


def normalize_reference_station(value: str) -> str:
    match = STATION_RE.search(value.replace("～", "-").replace("~", "-"))
    return normalize_station_match(match) or value


def stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"
