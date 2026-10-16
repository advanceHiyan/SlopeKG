"""Retain competing source values; detection does not adjudicate truth/scope."""
from collections import defaultdict

FACT_FIELDS = frozenset({"side", "slope_type", "material_nature", "slope_length_m",
    "slope_height_min_m", "slope_height_max_m", "slope_gradient_min_deg",
    "slope_gradient_max_deg", "slope_aspect_deg"})

def source_disagreements(groups: tuple[list[dict], ...]) -> dict[tuple[str, str], list[dict]]:
    candidates = defaultdict(lambda: defaultdict(list))
    for group in groups:
        for row in group:
            key = (str(row.get("route_code") or "ROUTE"), row["station"])
            evidence = {k:row.get(k) for k in ("document_id", "page", "method")}
            evidence["block_ids"] = row.get("evidence_block_ids", [row.get("evidence_block_id")])
            for field in FACT_FIELDS:
                if row.get(field) not in (None, ""):
                    candidates[key][field].append(dict(value=row[field], evidence=evidence))
            for item in row.get("_section_value_candidates", []):
                candidates[key][item["field"]].append(dict(value=item["value"], evidence=item["evidence"]))
    output = {}
    for key, fields in candidates.items():
        issues = []
        for field, rows in fields.items():
            alternatives = []
            for row in rows:
                # Numeric 15 and 15.0 express the same value; zero is retained.
                alternative = next((r for r in alternatives if r["value"] == row["value"]), None)
                if alternative is None:
                    alternative = dict(value=row["value"], sources=[])
                    alternatives.append(alternative)
                if row["evidence"] not in alternative["sources"]:
                    alternative["sources"].append(row["evidence"])
            if len(alternatives) > 1:
                issues.append(dict(kind="potential_source_disagreement", field=field,
                    alternatives=alternatives, review_status="needs_scope_and_source_review",
                    resolution="unresolved_legacy_value_not_adjudicated"))
        output[key] = sorted(issues, key=lambda row:row["field"])
    return output
