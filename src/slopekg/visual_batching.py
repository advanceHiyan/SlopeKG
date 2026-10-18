"""Lossless prompt partitioning and audit inventories; no model or PDF I/O."""
import json
from .visual_contract import ContractError, FACT_KEYS


def compact(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)



def pack(units: list, budget: int) -> list[list]:
    """Partition all units; never silently truncate a tail to meet a budget."""
    batches, current, size = [], [], 2
    for unit in units:
        cost = len(compact(unit)) + 1
        if cost + 2 > budget:
            raise ContractError("One structured item exceeds prompt budget")
        if current and size + cost > budget:
            batches.append(current)
            current, size = [], 2
        current.append(unit)
        size += cost
    if current:
        batches.append(current)
    return batches



def audit_units(region: dict, facts: list[dict], budget: int) -> list[dict]:
    """Lossless audit shards; a long table/paragraph must not block its page."""
    common={"region":region["id"],"bbox":region["bbox"],"status":region["status"]}
    reading_data=region.get("reading",{})
    units=[]
    transcript=reading_data.get("transcript","")
    size=max(256,budget//4)
    for offset in range(0,len(transcript),size):
        units.append({**common,"part":"transcript","offset":offset,"total_chars":len(transcript),
                      "transcript":transcript[offset:offset+size]})
    for table_index,table in enumerate(reading_data.get("tables",[])):
        meta={k:v for k,v in table.items() if k!="cells"}
        for cell in table.get("cells",[]):
            units.append({**common,"part":"table_cell","table_index":table_index,"table":meta,"cell":cell})
    for dimension in reading_data.get("dimensions",[]):
        units.append({**common,"part":"dimension","dimension":dimension})
    for item in region.get('content_coverage',[]):
        if not item['accounted']:
            units.append({**common,'part':'unaccounted_source_content','content':item})
    for fact in facts:
        units.append({**common,"part":"fact","fact":{k:fact[k] for k in FACT_KEYS},
                      "fact_id":fact["id"],"fact_status":fact["status"],"reason":fact.get("reason",""),
                      "admission_checks":fact.get("admission_checks",{})})
    return units or [{**common,"part":"empty_or_unread"}]



def layout_anchors(anchors: list[dict], budget: int) -> tuple[list[dict], str]:
    """Optional layout hints must fit as a whole, never abort visual layout.

    Quantization affects only the wire hints; original cells remain the sole
    crop geometry. Extremely dense pages fall back to image-led layout, then
    source-cell containment and independent unassigned-cell audit.
    """
    if len(compact(anchors))<=budget:return anchors,"full"
    geometry=[{"id":a["id"],"bbox":[round(v,6) for v in a["bbox"]]} for a in anchors]
    if len(compact(geometry))<=budget:return geometry,"geometry_only"
    return [],"image_layout_source_geometry_retained_locally"



def verification_batches(candidates: list[dict], budget: int, crop_slots: int, *, shared_region: bool = False) -> list[list[dict]]:
    """Batch by distinct evidence images and text capacity, not one image per fact."""
    result=[]
    for part in pack(candidates,budget):
        batch=[]
        boxes=set()
        for fact in part:
            box=compact(fact["bbox"])
            if batch and (len(batch)>=(12 if crop_slots or shared_region else 6) or
                          (not shared_region and crop_slots and box not in boxes and len(boxes)>=crop_slots)):
                result.append(batch);batch=[];boxes=set()
            batch.append(fact);boxes.add(box)
        if batch:result.append(batch)
    return result
