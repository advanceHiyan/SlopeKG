"""Prepare → resolve → execute tables → persist → review, for selected cases."""
from __future__ import annotations

import copy
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import uuid

from .adapters import PilotAdapter, clean, knowledge_candidates, sha
from .contract import (ADAPTER_VERSION, CONTRACT_VERSION, VersionConflict, applicability,
                       encoded, enumeration, fact_record, identity, literal, validate_fact)
from .rules import coefficient_axis, load_rules, table15, table17
from .store import AssessmentStore
from .workflow import AssessmentWorkflow
from ..provenance import source_revision

PACKAGE_LOADED_REVISION=source_revision(Path(__file__).parent)


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slot(fact):
    return fact["variable_code"] + ((":"+str(fact.get("feature_code"))) if fact["variable_code"] in {"observations", "reference", "geological_context"} else "")


class StandardAssessmentService(AssessmentWorkflow):
    def __init__(self, paths, *, database: Path | None = None, curated_evidence: Path | None = None, judgment_model=None):
        self.paths = paths
        self.store = AssessmentStore(database or paths.output_dir / "assessment-pilot/assessment.sqlite")
        self.curated_evidence = curated_evidence
        self.judgment_model = judgment_model
        self.rules = load_rules(paths.root)
        self.rule_version = self.rules["rule_version"]
        self.rules_hash = hashlib.sha256(encoded(self.rules).encode()).hexdigest()

    def prepare_assessment(self, request: dict) -> dict:
        self._request_version(request)
        if request.get('supplied_assertions'):
            raise ValueError('pilot imports existing recognition/source records; new manual assertions require a registered source import, not an unregistered payload')
        if request.get("task", "landslide_current") != "landslide_current":
            raise ValueError("pilot supports landslide_current only")
        if not isinstance(request.get("target_ref"), str):
            raise ValueError("target_ref required")
        adapter = PilotAdapter(self.paths, self.curated_evidence)
        data = adapter.build(request["target_ref"], request.get("time_selector"))
        case = {"case_id": "case_"+uuid.uuid4().hex, "contract_version": CONTRACT_VERSION,
                "target": "landslide_current", "target_ref": data["target_ref"], "slope_id": data["slope_id"],
                "scope": data["scope"], "source_snapshot_id": identity("snapshot_", data["source_hashes"]),
                "source_hashes": data["source_hashes"], "requested_output": "table17_candidate",
                "resolution_version": 1, "fact_ids": [f["fact_id"] for f in data["facts"]],
                "context_ids": [c["context_id"] for c in data["contexts"]], "created_at": timestamp(),
                "selections": {}, "hazard_type_supported": data["hazard_type_supported"],
                "source_coverage": "selected_existing_graph_records_and_explicit_curated_quotes_not_full_document_search",
                "adapter_version": ADAPTER_VERSION}
        with self.store.transaction() as db:
            for name, key in [("source_record", "source_records"), ("evidence_context", "contexts"), ("identity_binding", "bindings"), ("fact_assertion", "facts")]:
                for r in data[key]:
                    self.store.put(name, r, db)
            self.store.put("assessment_case", case, db)
        return {"case": case, "fact_count": len(case["fact_ids"]), "execution_status": "not_run",
                "runtime_scope": "isolated_pilot_database"}

    def _request_version(self, request):
        if not isinstance(request, dict) or request.get("contract_version") != CONTRACT_VERSION:
            raise ValueError("unsupported or missing contract_version")

    def _case(self, case_id, version, db):
        case = self.store.get("assessment_case", case_id, db)
        if type(version) is not int:
            raise ValueError('expected_resolution_version must be an integer')
        if version != case["resolution_version"]:
            raise VersionConflict("resolution_version changed; read current case before evaluating/reviewing")
        return case

    def _facts(self, case, db):
        facts = [self.store.get("fact_assertion", fid, db) for fid in case["fact_ids"]]
        removed = {f["supersedes_fact_id"] for f in facts if f["supersedes_fact_id"]}
        return [f for f in facts if f["fact_id"] not in removed]

    def _resolve(self, case, facts, variable, db):
        candidates = [f for f in facts if slot(f) == variable]
        checks = [applicability(f, case) for f in candidates]
        usable = [f for f,c in zip(candidates,checks) if c["status"] != "incompatible" and f["value"] is not None
                  and f["normalization_status"] == "canonical" and c["dimensions"]["support"] != "unresolved"]
        chosen = case["selections"].get(variable)
        workspace_case = bool(case.get('workspace_slope_id'))

        def comparison_key(fact):
            value = fact['value']
            if workspace_case and value['kind'] == 'number' and value.get('qualifier') in (None, 'exact_as_reported'):
                # An omitted precision label does not disagree with the same
                # exact reading. Retain both original values and any explicit
                # approximate/rounded qualifiers on their own assertions.
                scalar = value['value']
                if type(scalar) is float and scalar.is_integer():
                    scalar = int(scalar)
                return encoded({**value, 'value': scalar, 'qualifier': 'exact_as_reported'})
            return encoded(value)

        groups = {comparison_key(f) for f in usable}
        if chosen:
            selected = next((f for f in usable if f['fact_id'] == chosen), None)
        elif len(groups) == 1:
            selected = next((f for f in usable if workspace_case and f['source_check']['content'] == 'literal_matched'), usable[0])
        else:
            selected = None
        conflict = len(groups)>1
        state = "selected" if selected else "conflicted" if conflict else "candidates_only" if candidates else "missing"
        # Preserve conflicting, unchecked extraction alternatives for review;
        # their disagreement is not automatically a confirmed geology conflict.
        raw_groups = {comparison_key(f) for f in candidates if f["value"] is not None}
        potential = len(raw_groups)>1 and not conflict
        resolution = {"case_id": case["case_id"], "variable_code": variable, "candidate_fact_ids": [f["fact_id"] for f in candidates],
                      "selected_fact_id": selected["fact_id"] if selected else None, "status": state,
                      "applicability_by_fact": checks, "conflict_groups": [{"kind":"value_conflict" if conflict else "potential_source_or_scope_conflict", "fact_ids":[f['fact_id'] for f in candidates], "resolved_by_selection": bool(chosen)}] if conflict or potential else [],
                      "missing": [], "resolution_version": case["resolution_version"]}
        resolution["resolution_id"] = identity("resolution_", resolution)
        self.store.put("case_resolution", resolution, db)
        basis = next((c["basis_status"] for c in checks if selected and c["fact_id"] == selected["fact_id"]), "unresolved")
        if chosen and case.get("selection_actor",{}).get(variable) != "human":
            basis = "conditional"
        return selected, resolution, basis

    def _derive(self, case, method, inputs, variable, value, scope, basis, db, *, detail=None, feature=None):
        run = {"case_id": case["case_id"], "resolution_version": case["resolution_version"], "method_id": method,
               "method_version": "pilot-1", "method_kind": "deterministic", "input_fact_ids": [f["fact_id"] for f in inputs],
               "rule_refs": ["GBT40112-T15"] if method in {"M-H-T15","M-BASIS-METRIC"} else ["GBT40112-T4-FS"] if method == "M-COEF-COMPARE" else ["GBT40112-T17"] if method == "M-T17" else [],
               "rule_version": self.rule_version, "rule_snapshot_hash": self.rules_hash, "assumptions": [], "missing_dependencies": [],
               "execution_status": "executed", "basis_status": basis, "detail": detail or {}}
        cache_key = {k:v for k,v in run.items() if k!='resolution_version'}
        cache_key.update(output_variable=variable,output_value=value,output_scope=scope,feature_code=feature)
        run["derivation_id"] = identity("derive_", cache_key)
        try:
            cached = self.store.get('derivation_run',run['derivation_id'],db)
        except KeyError:
            pass
        else:
            return self.store.get('fact_assertion',cached['output_fact_ids'][0],db), dict(cached,cache_reused=True)
        fact = fact_record(variable, value, scope, source_refs=[], evidence_refs=sorted({r for f in inputs for r in f["evidence_refs"]}),
                           origin="rule", semantic="derived_judgment" if variable in {"harm","stability_axis","hazard_level","assessment_basis"} else "derived_measurement",
                           role="calculation", feature=feature, derivation_id=run["derivation_id"], source_standard="GB/T 40112-2021" if variable in {"harm","hazard_level"} else "unknown")
        fact["basis_status"] = basis
        fact["fact_id"] = identity("fact_", {k:v for k,v in fact.items() if k!='fact_id'})
        run["output_fact_ids"] = [fact["fact_id"]]
        self.store.put("fact_assertion", fact, db)
        self.store.put("derivation_run", run, db)
        return fact, run

    def evaluate_assessment(self, request: dict, *, db=None) -> dict:
        """Evaluate in the caller's write transaction when part of a case edit."""
        self._request_version(request)
        if request.get("rule_version", self.rule_version) != self.rule_version:
            raise ValueError("unsupported rule_version")
        with (self.store.transaction() if db is None else nullcontext(db)) as db:
            case = self._case(request["case_id"], request.get("expected_resolution_version"), db)
            facts = self._facts(case, db)
            trace, axes, normals, adopted, reused = [], [], [], [], []
            # Operate on each original section/scenario, never on a pooled Fs.
            fs_rows = [f for f in facts if f['variable_code']=='fs' and isinstance(f['value'],dict) and f['value']['kind']=='number' and f['source_check']['content'] not in {'unchecked','unsupported'}
                       and f['scope']['time']==case['scope']['time']]
            for fs in fs_rows:
                targets = [f for f in facts if f['variable_code']=='fst' and isinstance(f['value'],dict) and f['value']['kind']=='number' and f['scope']==fs['scope'] and f['source_check']['content'] not in {'unchecked','unsupported'}]
                unique = {encoded(f['value']) for f in targets}
                if len(unique)>1:
                    axes.append({'fs_fact_id':fs['fact_id'],'axis':None,'reason':'conflicting_fst'})
                    continue
                fst = targets[0] if targets else None
                adopted.append({'method_id':'M-FS-REPORT','selected_fact_id':fs['fact_id'],'value':fs['value'],'scope':fs['scope'],'new_value_calculated':False})
                compared = coefficient_axis(fs,fst)
                if compared.get('axis'):
                    f, run = self._derive(case,'M-COEF-COMPARE',[fs]+([fst] if fst else []),'stability_axis',{'kind':'text','text':compared['axis']},fs['scope'],'conditional',db,detail=compared)
                    trace.append(run['derivation_id'])
                    if run.get('cache_reused'):
                        reused.append(run['derivation_id'])
                    axes.append({**compared,'output_fact_id':f['fact_id'],'basis_status':'conditional','scope':fs['scope'],'reason':'系数列对照；Fst适用性未专业确认；不产生development'})
            for raw in facts:
                v = raw['value']
                if raw['normalization_status']=='raw' and v and v['kind']=='interval' and v['unit']=='cm':
                    normalized = {**v,'unit':'m','lower':v['lower']*0.01 if v['lower'] is not None else None,'upper':v['upper']*0.01 if v['upper'] is not None else None}
                    f, run = self._derive(case,'M-UNIT-CM-M',[raw],raw['variable_code'],normalized,raw['scope'],'supported',db,feature=raw.get('feature_code'),detail={'factor':0.01,'approximate_preserved':True})
                    normals.append(f['fact_id']); trace.append(run['derivation_id'])
                    if run.get('cache_reused'):
                        reused.append(run['derivation_id'])
            resolutions = {}
            for variable in ['development','harm','assessment_basis','deaths','direct_loss_wan','threatened_people','potential_loss_wan']:
                resolutions[variable] = self._resolve(case,facts,variable,db)
            development, dr, dev_basis = resolutions['development']
            harm, hr, harm_basis = resolutions['harm']
            basis = resolutions['assessment_basis'][0]
            t15 = None
            conflicts = []
            for variable, (_, resolution, _) in resolutions.items():
                if resolution['status']=='conflicted':
                    conflicts.append({'kind':'unresolved_input_conflict','variable':variable,'fact_ids':resolution['candidate_fact_ids'],'blocks':['M-T17']})
            metric_variables = {'deaths','direct_loss_wan','threatened_people','potential_loss_wan'}
            metric_facts = []
            for f in facts:
                if f['variable_code'] not in metric_variables:
                    continue
                selected = case['selections'].get(f['variable_code'])
                if selected and selected != f['fact_id']:
                    continue
                app = applicability(f,case)
                if app['status']!='incompatible' and app['dimensions']['support']=='compatible' and f['normalization_status']=='canonical' and f['value'] is not None:
                    metric_facts.append(f)
            families={self.rules['table15'][f['variable_code']]['basis'] for f in metric_facts}
            if not basis and resolutions['assessment_basis'][1]['status']!='conflicted' and len(families)==1:
                inferred_basis=next(iter(families))
                input_basis='supported' if all(applicability(f,case)['basis_status']=='supported' for f in metric_facts) else 'conditional'
                basis, run=self._derive(case,'M-BASIS-METRIC',metric_facts,'assessment_basis',enumeration('assessment_basis',inferred_basis),case['scope'],input_basis,db,
                                       detail={'reason':'已类型化的死亡/直接损失属于灾情；受威胁人数/可能损失属于险情，不新增人工必填。'})
                trace.append(run['derivation_id'])
                if run.get('cache_reused'):reused.append(run['derivation_id'])
                resolutions['assessment_basis']=(basis,{**resolutions['assessment_basis'][1], 'status':'derived',
                                                        'selected_fact_id':basis['fact_id'],'derivation_id':run['derivation_id']},input_basis)
            if basis and metric_facts:
                t15 = table15(literal(basis['value']),metric_facts,self.rules)
                applicable = [f for f in metric_facts if f['fact_id'] in t15['input_fact_ids']]
                if t15['conflict']:
                    conflicts.append({'kind':'table15_indicator_conflict','indicator_results':t15['indicator_results'],'blocks':['M-T17']})
                if t15['harm']:
                    input_basis = 'supported' if all(resolutions[k][2]=='supported' for k in ['assessment_basis']+[f['variable_code'] for f in applicable]) else 'conditional'
                    derived, run = self._derive(case,'M-H-T15',[basis]+applicable,'harm',enumeration('harm',t15['harm']),case['scope'],input_basis,db,detail=t15)
                    trace.append(run['derivation_id'])
                    if run.get('cache_reused'):
                        reused.append(run['derivation_id'])
                    if harm and literal(harm['value']) != t15['harm']:
                        conflicts.append({'kind':'reported_vs_derived_harm','fact_ids':[harm['fact_id'],derived['fact_id']],'blocks':['M-T17']})
                        harm = None
                    elif not harm:
                        harm, harm_basis = derived, input_basis
            developments = [literal(development['value'])] if development else []
            harms = [literal(harm['value'])] if harm else (t15['possible_grades'] if t15 else [])
            if t15 and t15['conflict']:
                harms = sorted(set(harms+t15['possible_grades']))
            lookup = table17(developments,harms,self.rules)
            if conflicts:
                lookup['grade'] = None
            status = 'supported' if lookup['grade'] and dev_basis=='supported' and harm_basis=='supported' else 'conditional' if lookup['branches'] else 'unresolved'
            if not case['hazard_type_supported']:
                lookup = {'grade':None,'possible_grades':[],'branches':[]}
                status = 'unresolved'
            elif lookup['grade'] and development and harm:
                _, run = self._derive(case,'M-T17',[development,harm],'hazard_level',enumeration('hazard_level',lookup['grade']),case['scope'],status,db,detail=lookup)
                trace.append(run['derivation_id'])
                if run.get('cache_reused'):
                    reused.append(run['derivation_id'])
            missing = []
            if not development:
                missing.append({'variable_or_feature':'development','reason':'no_adoptable_judgment','blocks_method_ids':['M-T17'],
                                'next_action':'derive' if any(f['variable_code']=='observations' for f in facts) else 'retrieve','user_input_required':False,
                                'method_status':'M-D-JUDGMENT_available','why_this_resolves_target':'利用表4/5和已有观察提出有据建议；证据不足时保留弃答，再核对实际缺少的观察。'})
            if not harms:
                missing.append({'variable_or_feature':'harm','reason':'no_applicable_grade_or_one_metric','blocks_method_ids':['M-H-T15','M-T17'],
                                'next_action':'retrieve','user_input_required':False,'searched_source_refs':list(case['source_hashes']),
                                'why_this_resolves_target':'核对已检索的同对象同期危害结论，或一项灾情/险情人数、损失指标；候选原句未确认时保留缺口，历史死亡数不代替当前威胁人数。'})
            if lookup['grade']:
                missing.append({'variable_or_feature':'triggers_and_geological_context_review','reason':'complete_workflow_context_not_confirmed',
                                'blocks_method_ids':[],'next_action':'review','user_input_required':False,'why_this_resolves_target':'保留格值；完整现状评估仍需环境和诱因综合复核。'})
            for item in missing:
                item.setdefault('scope',case['scope'])
                item.setdefault('searched_source_refs',list(case['source_hashes']))
                item.setdefault('alternatives_tried',['existing_applicable_judgment','available_rule_or_context_branch'])
            # Context-only extraction disagreements, independent of table inputs.
            scalar_features = {'slope_height_max_m','slope_height_min_m','slope_gradient_min_deg','slope_gradient_max_deg','slope_aspect_deg','material_nature'}
            for variable in sorted({slot(f) for f in facts if f['variable_code']=='observations' and f.get('feature_code') in scalar_features}):
                _, resolution, _ = self._resolve(case,facts,variable,db)
                for group in resolution['conflict_groups']:
                    conflicts.append({**group,'variable':variable,'blocks':[]})
            context_task = {'method_id':'M-D-JUDGMENT','execution_status':'not_run','context_fact_ids':[f['fact_id'] for f in facts if f['variable_code'] in {'observations','geological_context','body_mean_angle'}],
                            'rule_refs':['GBT40112-T4-CONTEXT','GBT40112-T5-CONTEXT'],'reason':'无完备布尔判据；未调用模型或专业人员，不制造发育程度。'}
            if case.get('judgment_run_id'):
                judgment = self.store.get('derivation_run', case['judgment_run_id'], db)
                context_task = {**context_task, 'execution_status':judgment['execution_status'],
                                'derivation_id':judgment['derivation_id'], 'detail':judgment['detail'],
                                'reason':'模型依据原文与国标提出建议或弃答，实际人工确认单独保存。'}
            active_reviews = [r for r in self.store.rows('review_decision', db)
                              if r['case_id']==case['case_id'] and r.get('accepted_resolution_version')==case['resolution_version']]
            accepted = next((r for r in reversed(active_reviews) if r['decision']=='confirm_result'
                             and r.get('rule_snapshot_hash')==self.rules_hash), None)
            reviewed_context = bool(accepted and accepted.get('context_reviewed'))
            if reviewed_context:
                missing = [m for m in missing if m['variable_or_feature']!='triggers_and_geological_context_review']
            # A supplied interval can legitimately span several table cells.
            # Keep those outcomes for resolution; do not claim the input is
            # missing, choose a midpoint, or request an invented scalar value.
            branched_outcomes = not lookup['grade'] and len(lookup['possible_grades']) > 1
            result = {'case_id':case['case_id'],'contract_version':CONTRACT_VERSION,'rule_version':self.rule_version,
                      'input_snapshot_hash':hashlib.sha256(encoded({'facts':facts,'case':case}).encode()).hexdigest(),
                      'resolution_version':case['resolution_version'],'execution_status':'executed',
                      'rule_snapshot_hash':self.rules_hash,'reused_derivation_ids':reused,
                      'table17_result':{'grade':lookup['grade'],'possible_grades':lookup['possible_grades'],'conditions':[] if status=='supported' else ['仅在已列分支和适用前提下成立'] if lookup['branches'] else []},
                      'table17_branches':lookup['branches'],'basis_status':status,
                      'workflow_status':'invalid_request' if not case['hazard_type_supported'] else 'needs_resolution' if any(c['blocks'] for c in conflicts) or branched_outcomes else 'reviewed' if accepted else 'ready_for_review' if lookup['grade'] else 'needs_evidence',
                      'review_status':'accepted' if accepted else 'pending','formal_assessment_enabled':False,
                      'resolved_variables':{k:v[1] for k,v in resolutions.items()},'trace_derivation_ids':trace,
                      'minimal_missing':missing,'conflicts':conflicts,'context_refs':case['context_ids'],
                      'partials':{'source_adoptions':adopted,'stability_axes':axes,'normalizations':normals,'table15':t15,'professional_context_task':context_task},
                      'source_coverage':case['source_coverage'], 'retrieval':case.get('retrieval'),
                      'assessment_scope':case['scope'], 'review_decision_id':accepted['review_id'] if accepted else None,
                      'environment_and_triggers':self.context_assessment(case,facts),
                      'review_attention':[r for r in self.store.rows('review_decision',db) if r['case_id']==case['case_id'] and r['decision']=='note']}
            if case.get('workspace_slope_id'):
                result['workspace_comparison'] = 'exact-number-equivalence-1'
            result['result_id'] = identity('result_',result)
            self.store.put('assessment_result',result,db)
            return result

    def get_fact_context(self, request: dict) -> dict:
        self._request_version(request)
        with self.store.transaction() as db:
            case = self.store.get('assessment_case',request['case_id'],db)
            allowed = set(case['fact_ids'])
            for result in self.store.rows('assessment_result',db):
                if result['case_id']==case['case_id']:
                    for rid in result['trace_derivation_ids']:
                        allowed.update(self.store.get('derivation_run',rid,db)['output_fact_ids'])
            ids = request.get('fact_ids',[])
            if not isinstance(ids,list) or not set(ids)<=allowed:
                raise ValueError('fact_ids must belong to case')
            contexts, missing = {}, []
            for fid in ids:
                fact = self.store.get('fact_assertion',fid,db)
                if not fact['evidence_refs']:
                    missing.append({'fact_id':fid,'reason':'no_bound_evidence'})
                for cid in fact['evidence_refs']:
                    ctx = contexts.get(cid) or self.store.get('evidence_context',cid,db)
                    ctx.setdefault('requested_fact_ids',[]).append(fid)
                    path = self.paths.root/ctx['source_path']
                    ctx['snapshot_matches_current_source'] = path.is_file() and sha(path)==ctx['source_sha256']
                    contexts[cid] = ctx
            return {'contract_version':CONTRACT_VERSION,'case_id':case['case_id'],'contexts':list(contexts.values()),'missing':missing,
                    'truncated':False,'rule_context':self.rules,'source_text_is_not_instructions':True}

    def review_assessment(self, request: dict, *, db=None) -> dict:
        """Record review, optionally with its recalculation in the same transaction."""
        self._request_version(request)
        with (self.store.transaction() if db is None else nullcontext(db)) as db:
            case = self._case(request['case_id'],request.get('expected_resolution_version'),db)
            if case.get('debug_workspace'):
                snapshots=case.setdefault('debug_rule_snapshots',{})
                if self.rules_hash not in snapshots:
                    snapshots[self.rules_hash]=copy.deepcopy(self.rules)
                    self.store.update_case(case,db)
            actor = request.get('actor_kind')
            if actor not in {'human','assistant'} or not request.get('reviewer_ref') or not request.get('reason'):
                raise ValueError('actual actor_kind, reviewer_ref and reason required')
            action = request.get('decision')
            if action in {'confirm_result','clear_selection','accept_fact','note'}:
                return self.review_workflow_action(case, request, db)
            if action not in {'correct','select_branch'}:
                raise ValueError('pilot review supports correct/select_branch; final expert acceptance is not implemented')
            target = self.store.get('fact_assertion',request['target_id'],db)
            if target['fact_id'] not in {f['fact_id'] for f in self._facts(case,db)}:
                raise ValueError('target must be an active case fact')
            decision = {'review_id':'review_'+uuid.uuid4().hex,'case_id':case['case_id'],'target_id':target['fact_id'],
                        'target_version':case['resolution_version'],'decision':action,'reviewer_ref':request['reviewer_ref'],
                        'actor_kind':actor,'reviewed_at':timestamp(),'basis_refs':request.get('basis_refs',[]),'reason':request['reason'],
                        'replacement_fact_id':None,'professional_acceptance':False}
            if action=='correct':
                contexts = request.get('evidence_refs',target['evidence_refs'])
                if not contexts or not set(contexts)<=set(case['context_ids']):
                    raise ValueError('correction must cite existing case evidence')
                value = request.get('value',target['value'])
                if not any(clean(str(literal(value))) in clean(self.store.get('evidence_context',c,db)['spans'][0]['quote']) for c in contexts):
                    raise ValueError('corrected value must be supported by cited source text; complex changes need a new source record')
                decision_source = {'record_ref_id':identity('source_',decision),'original_record':copy.deepcopy(decision),
                                   'source_path':'operator_record','snapshot_sha256':'in_database','collection':'review_decision','id_key':'review_id','record_id':decision['review_id']}
                revised = fact_record(target['variable_code'],value,target['scope'],source_refs=[decision_source['record_ref_id']]+target['source_record_refs'],
                    evidence_refs=contexts,origin='manual' if actor=='human' else 'manual_transcription',semantic=target['semantic_kind'],role=target['role'],
                    feature=target.get('feature_code'),source_standard=target['source_standard'],support='literal_matched',normalization=target['normalization_status'],supersedes=target['fact_id'])
                validate_fact(revised)
                decision['replacement_fact_id'] = revised['fact_id']
                decision_source['original_record'] = copy.deepcopy(decision)
                self.store.put('source_record',decision_source,db)
                self.store.put('fact_assertion',revised,db)
                case['fact_ids'].append(revised['fact_id'])
                if case['selections'].get(slot(target)) == target['fact_id']:
                    case['selections'][slot(target)] = revised['fact_id']
            else:
                case['selections'][slot(target)] = target['fact_id']
                case.setdefault('selection_actor',{})[slot(target)] = actor
            case['resolution_version'] += 1
            self.store.put('review_decision',decision,db)
            self.store.update_case(case,db)
            return {'review_decision':decision,'case':case,'previous_results_retained':True,'recompute_required':True,'review_status':'pending'}

    def import_knowledge(self, fact_ids: list[str]) -> dict:
        bundle = knowledge_candidates(self.paths,fact_ids)
        with self.store.transaction() as db:
            for row in bundle['rows']:
                self.store.put('source_record',row['source'],db)
                self.store.put('evidence_context',row['context'],db)
                self.store.put('fact_assertion',row['fact'],db)
        return bundle

    def get_case(self, case_id: str) -> dict:
        with self.store.transaction() as db:
            case = self.store.get('assessment_case',case_id,db)
            results = [r for r in self.store.rows('assessment_result',db) if r['case_id']==case_id]
            current=[r for r in results if r['resolution_version']==case['resolution_version'] and r['rule_snapshot_hash']==self.rules_hash]
            if case.get('workspace_slope_id'):
                current = current[-1:]
            current_ids = {r['result_id'] for r in current}
            derived_ids={fid for r in current for did in r['trace_derivation_ids'] for fid in self.store.get('derivation_run',did,db)['output_fact_ids']}
            return {'case':case,'facts':self._facts(case,db),'derived_facts':[self.store.get('fact_assertion',fid,db) for fid in sorted(derived_ids)],
                    'results':[dict(r,is_current=r['result_id'] in current_ids) for r in results],
                    'contexts':[self.store.get('evidence_context',cid,db) for cid in case['context_ids']],
                    'derivations':[d for d in self.store.rows('derivation_run',db) if d['case_id']==case_id],
                    'reviews':[d for d in self.store.rows('review_decision',db) if d['case_id']==case_id]}
