"""Read selected legacy records into the contract without publishing back."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re

from .contract import ADAPTER_VERSION, VOCABS, encoded, enumeration, fact_record, identity, number


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clean(text: str) -> str:
    return re.sub(r"\s+", "", text)


def section_key(text: str) -> str:
    return text.replace("Ⅱ", "II").replace("Ⅰ", "I").replace("'", "").replace("′", "").replace("断面", "").strip()


class PilotAdapter:
    def __init__(self, paths, curated_evidence: Path | None = None):
        self.paths, self.root = paths, paths.root
        self.graph = read_json(paths.graph_json)
        self.graph_hash = sha(paths.graph_json)
        self.nodes = {n["id"]: n for n in self.graph["nodes"]}
        self.evidence = {e["id"]: e for e in self.graph["evidence"]}
        self.documents = {}
        docs = paths.parsed_dir / "documents.jsonl"
        if docs.exists():
            self.documents = {r["id"]: r for r in map(json.loads, docs.read_text(encoding="utf-8-sig").splitlines()) if r}
        self.curated_path = curated_evidence
        self.curated = read_json(curated_evidence) if curated_evidence else {"evidence": []}
        self.records, self.contexts, self.facts = {}, {}, []
        self.source_hashes = {str(paths.graph_json.relative_to(self.root)): self.graph_hash}
        if curated_evidence:
            self.source_hashes[str(curated_evidence.relative_to(self.root))] = sha(curated_evidence)
        self.page_cache = {}

    def register(self, path: Path, collection: str, rid: str, record: dict, snapshot: str | None = None) -> str:
        ref = {"source_path": str(path.relative_to(self.root)), "snapshot_sha256": snapshot or sha(path),
               "collection": collection, "id_key": "id", "record_id": rid, "original_record": record}
        ref["record_ref_id"] = identity("source_", ref)
        self.records[ref["record_ref_id"]] = ref
        self.source_hashes[ref["source_path"]] = ref["snapshot_sha256"]
        return ref["record_ref_id"]

    def page_context(self, source_path: Path, page: int) -> str:
        key = str(source_path), page
        if key in self.page_cache:
            return self.page_cache[key]
        import fitz
        source_hash = sha(source_path)
        with fitz.open(source_path) as document:
            text = document[page-1].get_text()
        self.source_hashes[str(source_path.relative_to(self.root))] = source_hash
        cid = self.context(source_path, source_hash, page, text, "page_text", support="unchecked")
        self.page_cache[key] = cid
        return cid

    def context(self, path: Path, source_hash: str, page: int | None, quote: str, kind: str,
                *, support="literal_matched", bbox=None, table=None, original=None) -> str:
        value = {"context_version": "pilot-context-1", "fact_ids": [], "source_record_refs": [],
                 "source_sha256": source_hash, "source_path": str(path.relative_to(self.root)),
                 "spans": [{"file_page_1_based": page, "printed_page": None, "quote": quote,
                            "kind": kind, "bbox": bbox, "coordinate_space": "pdf_points" if bbox else "unknown",
                            "support_status": support}], "table_context": table or {}, "figure_context": {"status": "not_loaded"},
                 "related_evidence_refs": [], "support_links": [],
                 "coverage": {"whole_document": False, "selected_quote": True, "truncated": False}, "omitted_refs": []}
        if original is not None:
            value["original_record"] = original
        value["content_hash"] = hashlib.sha256(encoded(value).encode()).hexdigest()
        value["context_id"] = identity("context_", value)
        self.contexts[value["context_id"]] = value
        return value["context_id"]

    def basic_context(self, evidence: dict) -> str:
        doc = self.documents.get(evidence.get("document_id"), {})
        path = self.root / str(doc.get("path") or ("data/rawPDF/" + evidence.get("source_file", "unknown"))).replace("\\", "/")
        source_hash = doc.get("source_sha256", "unknown")
        if path.is_file():
            source_hash = sha(path)
            self.source_hashes[str(path.relative_to(self.root))] = source_hash
        text = evidence.get("text", "")
        table = {"raw_table_text": text} if "工况" in text and "系数" in text else {}
        return self.context(path, source_hash, evidence.get("page"), text, evidence.get("kind", "unknown"),
                            bbox=evidence.get("bbox"), table=table, original=evidence)

    def add(self, variable, value, scope, source, evidence, **kwargs):
        fact = fact_record(variable, value, scope, source_refs=[source], evidence_refs=evidence, **kwargs)
        self.facts.append(fact)
        return fact

    def build(self, target: str, time_selector: dict | None = None) -> dict:
        matches = [n for n in self.nodes.values() if n["type"] == "Slope" and (n["id"] == target or n.get("props", {}).get("slope_id") == target)]
        if len(matches) != 1:
            raise ValueError("target_ref must resolve to exactly one existing slope record")
        slope = matches[0]
        slope_id = slope["props"]["slope_id"]
        station = clean(slope["props"].get("source_alias", ""))
        route = slope["props"].get("route_code_cache", "")
        # Explicit source-local replay identity. No cross-document same_as claim.
        subject = {"source_object_ref": slope_id + "/report-body", "canonical_object_id": slope["id"],
                   "object_kind": "landslide_body" if "滑坡" in slope.get("summary", "") else "slope_asset",
                   "binding_id": "binding_"+slope_id+"_"+self.graph_hash[:12], "spatial_extent_ref": None}
        period = {"kind": "unknown", "start": None, "end": None, "raw": "未解析资料期", "basis_refs": []}
        for e in self.curated["evidence"]:
            if route in e.get("scope", "") and e.get("field") == "survey_period":
                v = e["reported_value"]
                period = {"kind": "report_period", "start": v["from"], "end": v["to"], "raw": "报告共同外业时段；点位日未知", "basis_refs": [e["evidence_id"]]}
                break
        scope = {"subject": subject, "time": period,
                 "scenario": {"code": "unspecified", "raw": "报告现状资料；各计算工况另列", "basis_refs": []}}
        scope_record = self.register(self.paths.graph_json, "nodes", slope["id"], slope, self.graph_hash)
        self.add("hazard_type", {"kind": "text", "text": slope.get("summary") or "灾种未确认"}, scope,
                 scope_record, [], origin="basic", support="unchecked")
        links = [e for e in self.graph["edges"] if e["source"] == slope["id"]]
        for edge in links:
            node = self.nodes.get(edge["target"], {})
            props = node.get("props", {})
            evidence_id = edge.get("props", {}).get("evidence")
            evidence = self.evidence.get(evidence_id, {}) if isinstance(evidence_id, str) else {}
            context_ids = [self.basic_context(evidence)] if evidence else []
            record = self.register(self.paths.graph_json, "nodes", node.get("id", edge["target"]), node, self.graph_hash)
            scopes = copy.deepcopy(scope)
            if node.get("type") == "StabilityAnalysis":
                section = section_key(props.get("section") or "unknown")
                scopes["subject"].update(source_object_ref=subject["source_object_ref"]+"/section/"+section, object_kind="section")
                cond = props.get("condition", "")
                scopes["scenario"] = {"code": "natural" if "天然" in cond else "rainstorm" if "暴雨" in cond else "unspecified", "raw": cond, "basis_refs": context_ids}
                text = clean(evidence.get("text", ""))
                for variable, key in [("fs", "fs"), ("fst", "required_fs")]:
                    v = props.get(key)
                    if type(v) not in {int, float}:
                        continue
                    row_match = any(cond in line and str(props.get("fs")) in line and str(props.get("required_fs")) in line for line in evidence.get("text", "").splitlines())
                    supported = "工况" in text and "稳定性系数" in text and "安全系数" in text and row_match
                    f = self.add(variable, number(v, "1"), scopes, record, context_ids, origin="basic", semantic="reported_calculation", role="calculation", support="literal_matched" if supported else "unchecked")
                    if variable == "fst":
                        f["source_standard"] = "报告适用安全系数；GB适用性未审查"
                        # Re-key after adding a semantic qualifier; never mutate a stored fact.
                        f["fact_id"] = identity("fact_", {k:v for k,v in f.items() if k!="fact_id"})
                if props.get("status"):
                    self.add("reference", {"kind": "text", "text": props["status"]}, scopes, record, context_ids,
                             origin="basic", semantic="reported_judgment", role="calculation", feature="reported_stability_class",
                             support="literal_matched" if props["status"] in text else "unchecked")
            elif node.get("type") == "DeformationObservation":
                text = props.get("description") or node.get("summary") or node.get("label", "")
                support = "literal_matched" if clean(text) in clean(evidence.get("text", "")) else "unsupported" if evidence else "unchecked"
                self.add("observations", {"kind": "text", "text": text}, scopes, record, context_ids,
                         origin="basic", feature="deformation_description", support=support)
            elif node.get("type") in {"Lithology", "Stratum", "HydrologyObservation", "CausalFactor", "TerrainSetting", "StructuralPlane"}:
                self.add("geological_context", {"kind": "text", "text": node.get("label") or node["id"]}, scopes, record, context_ids,
                         origin="basic", feature=node["type"], support="unchecked")

        # Existing disagreements are source candidates, not proven physical conflicts.
        for disagreement in slope["props"].get("source_disagreements", []):
            for alternative in disagreement["alternatives"]:
                self.add("observations", number(alternative["value"], "m" if "height" in disagreement["field"] else "deg") if type(alternative["value"]) in {int,float} else {"kind":"text","text":str(alternative["value"])}, scope,
                         scope_record, [], origin="basic", feature=disagreement["field"], support="unchecked")

        # Bring the already curated ORIGINAL citations as an explicitly separate
        # transcription source, after checking each quote against the actual PDF.
        for e in self.curated["evidence"]:
            raw_scope = e.get("scope", "")
            belongs_document = Path(e["source_path"]).name.startswith(route+"-")
            station_matches = station and station.replace("-", "~") in clean(raw_scope).replace("～", "~").replace("-", "~")
            is_zoned_g344 = route == "G344" and belongs_document
            if not belongs_document or not (station_matches or is_zoned_g344 or e.get("field") == "survey_period"):
                continue
            path = self.root / e["source_path"]
            if sha(path) != e["source_pdf_sha256"]:
                raise ValueError("curated source PDF changed")
            page_id = self.page_context(path, e["file_page_1_based"])
            page_text = self.contexts[page_id]["spans"][0]["quote"]
            if clean(e["exact_quote"]) not in clean(page_text):
                raise ValueError("curated quote no longer matches original PDF")
            cid = self.context(path, e["source_pdf_sha256"], e["file_page_1_based"], e["exact_quote"], "reviewed_original_quote", original=e)
            source = self.register(self.curated_path, "evidence", e["evidence_id"], e)
            field, value = e.get("field"), e.get("reported_value")
            local_scope = copy.deepcopy(scope)
            if is_zoned_g344:
                if "古滑坡" in raw_scope and "新滑坡" not in raw_scope:
                    local_scope["subject"]["source_object_ref"] += "/old-landslide"
                elif field == "zone_I_status":
                    local_scope["subject"].update(source_object_ref=subject["source_object_ref"]+"/zone/I", object_kind="zone")
                elif field == "zone_II_status":
                    local_scope["subject"].update(source_object_ref=subject["source_object_ref"]+"/zone/II", object_kind="zone")
                elif "I/II区后缘共述" in raw_scope:
                    local_scope["subject"]["source_object_ref"] += "/common-rear"
            if field == "average_slope_angle":
                self.add("body_mean_angle", number(value["value"], "deg", "approximate"), scope, source, [cid], origin="manual_transcription", support="literal_matched")
            elif field in {"development", "development_level", "harm", "harm_level"}:
                variable = "development" if field.startswith("development") else "harm"
                label = value.get("value") if isinstance(value,dict) else value
                if label in VOCABS[variable][1]:
                    self.add(variable,enumeration(variable,label),local_scope,source,[cid],origin="manual_transcription",semantic="reported_judgment",
                             source_standard=e.get("source_standard","unknown"),support="literal_matched")
            elif field == "historical_event_deaths":
                hist = copy.deepcopy(scope)
                hist["time"] = {"kind":"event_period","start":None,"end":None,"raw":value["event_time"],"basis_refs":[cid]}
                hist["scenario"] = {"code":"event","raw":value["event_time"],"basis_refs":[cid]}
                self.add("deaths", number(value["deaths"], "person"), hist, source, [cid], origin="manual_transcription", support="literal_matched")
                self.add("assessment_basis", enumeration("assessment_basis", "灾情"), hist, source, [cid], origin="manual_transcription", support="literal_matched")
                self.add("observations", {"kind":"text","text":e['exact_quote']}, hist, source, [cid],
                         origin="manual_transcription", feature="historical_movement", support="literal_matched")
            elif field not in {"survey_period", "natural_stability", "storm_stability"}:
                text = e["exact_quote"]
                if field=='historical_event' and isinstance(value,str):
                    # A curated, source-checked ISO date is occurrence time, not
                    # the later report/survey window. Do not guess a date from
                    # unrelated rainfall dates in the paragraph.
                    event=re.search(r'(\d{4})-(\d{2})-(\d{2})',value)
                    if event:
                        y,m,d=map(int,event.groups())
                        if f'{y}年{m}月{d}日' in clean(text):
                            event_date=f'{y:04d}-{m:02d}-{d:02d}'
                            local_scope['time']={'kind':'event_period','start':event_date,'end':event_date,
                                                 'raw':'约'+event_date+'（原文回述事件）','basis_refs':[cid]}
                role='recommendation' if field=='proposed_treatment' else 'reference' if field=='other_standard_harm' else 'observation'
                self.add("reference" if role in {'recommendation','reference'} else "observations", {"kind":"text","text":text}, local_scope, source, [cid],
                         origin="manual_transcription", feature=field or "source_observation", support="literal_matched", role=role)
                if field == "rear_crack" and isinstance(value, dict) and value.get("width_cm"):
                    lo, hi = value["width_cm"]
                    self.add("observations", {"kind":"interval","lower":lo,"upper":hi,"lower_inclusive":True,"upper_inclusive":True,"unit":"cm","qualifier":"approximate","interval_semantics":"reported_range"}, local_scope, source, [cid], origin="manual_transcription", feature="rear_crack_width", support="literal_matched", normalization="raw")
        if time_selector:
            options = [f["scope"] for f in self.facts if f["scope"]["time"].get("raw") == time_selector.get("raw") and f["scope"]["time"].get("kind") == time_selector.get("kind")]
            if not options:
                raise ValueError("time_selector must select an existing source-supported scope")
            scope = copy.deepcopy(options[0])
        binding = {"binding_id": subject["binding_id"], "source_object_ref": subject["source_object_ref"],
                   "candidate_target_refs": [slope["id"]], "selected_target_ref": slope["id"], "relation": "located_in",
                   "scope_limit": "按现有台账对象建立报告回放，非跨资料物理体同一性专业确认", "basis_refs": [scope_record],
                   "mapping_status": "source_local_replay", "version": self.graph_hash}
        return {"target_ref": slope["id"], "slope_id": slope_id, "scope": scope, "facts": self.facts,
                "contexts": list(self.contexts.values()), "source_records": list(self.records.values()), "bindings": [binding],
                "source_hashes": self.source_hashes, "adapter_version": ADAPTER_VERSION, "hazard_type_supported": "滑坡" in slope.get("summary", "")}


def knowledge_candidates(paths, fact_ids: list[str]) -> dict:
    """Preserve selected standalone Knowledge facts; never guess a slope mapping."""
    path = paths.output_dir / "knowledge-recognition/run.json"
    run = read_json(path)
    requested = set(fact_ids)
    rows = [f for d in run.get("documents", []) for f in d.get("facts", []) if f.get("id") in requested]
    if {f["id"] for f in rows} != requested:
        raise ValueError("unknown Knowledge fact id")
    digest = sha(path)
    output = []
    for f in rows:
        scope = {"subject":{"source_object_ref":f['document_id']+":"+str(f.get('entity_id')), "canonical_object_id":None,"object_kind":"document_scope","binding_id":None,"spatial_extent_ref":None},
                 "time":{"kind":"unknown","start":None,"end":None,"raw":f.get('time') or "未提及","basis_refs":[]},
                 "scenario":{"code":"unspecified","raw":f.get('condition') or "未提及","basis_refs":[]}}
        ref = {"source_path":str(path.relative_to(paths.root)),"snapshot_sha256":digest,"collection":"documents[].facts","id_key":"id","record_id":f['id'],"original_record":f}
        ref['record_ref_id'] = identity('source_',ref)
        document = next(d for d in run['documents'] if d['id']==f['document_id'])
        raw_path = str(document.get('path','')).replace('\\','/')
        source_path = paths.root/raw_path
        context = {'context_version':'knowledge-preserved-context-1','fact_ids':[],
                   'source_record_refs':[ref['record_ref_id']],'source_sha256':f.get('source_sha256','unknown'),
                   'source_path':str(source_path.relative_to(paths.root)) if not Path(raw_path).is_absolute() else str(source_path),
                   'spans':[{'file_page_1_based':e.get('page'),'printed_page':None,'quote':e.get('quote',''),
                             'bbox':e.get('bbox'),'coordinate_space':'normalized_0_1','support_status':'model_supported' if f.get('status')=='verified_model' else 'unchecked'} for e in f.get('evidence',[])],
                   'table_context':{},'figure_context':{'status':'not_loaded'},'related_evidence_refs':[],
                   'support_links':[],'coverage':{'whole_document':False,'selected_quote':True,'truncated':False},'omitted_refs':[]}
        context['content_hash'] = hashlib.sha256(encoded(context).encode()).hexdigest()
        context['context_id'] = identity('context_',context)
        # These are source-level open fields. Reference thresholds and rock/joint
        # development must not be relabelled as current landslide inputs.
        candidate = fact_record('reference',{'kind':'text','text':str(f.get('value'))},scope,
          source_refs=[ref['record_ref_id']],evidence_refs=[context['context_id']],origin='knowledge',semantic='reference',role=f.get('role','unknown'),
          feature=f.get('field'),support='model_supported' if f.get('status')=='verified_model' else 'unchecked',normalization='unmapped')
        candidate['source_schema_version'] = run.get('schema_version')
        candidate['fact_id'] = identity('fact_', {k:v for k,v in candidate.items() if k!='fact_id'})
        output.append({'fact':candidate,'source':ref,'context':context,'rejection_reason':'reference_or_unmapped_scope_and_semantics','original_evidence':f.get('evidence',[])})
    return {'source_schema_version':run.get('schema_version'),'source_sha256':digest,'rows':output}
