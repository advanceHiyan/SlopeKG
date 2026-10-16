"""Build auditable review datasets without creating expert risk labels.

Public stability labels, source-report conclusions and model predictions remain
separate from the two project targets. Nothing here calls the extraction pipeline.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import statistics
from typing import Any
import xml.etree.ElementTree as ET

VERSION = 'risk-review-dataset-0.1'
PUBLIC_FIELDS = ('unit_weight_kn_m3', 'cohesion_kpa', 'friction_angle_deg',
                 'slope_angle_deg', 'height_m', 'pore_pressure_ratio')
PUBLIC_UNITS = ('kN/m3', 'kPa', 'degree', 'degree', 'm', 'dimensionless')
PRIORITIES = {'P1', 'P2', 'P3', 'P4'}
MODEL_FACT_TYPES = {'Lithology', 'Stratum', 'StructuralPlane', 'TerrainSetting',
                    'DeformationObservation', 'HydrologyObservation',
                    'ProtectionWork', 'MaterialParameterSet'}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def case_digest(case: dict) -> str:
    """Bind expert decisions to the assessed object, values and provenance."""
    return digest({k: case.get(k) for k in ('identity', 'input', 'provenance', 'source_type', 'parent_case_id')})


def jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(''.join(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n'
                            for row in rows), encoding='utf-8')


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def parse_public_stability(xml_path: Path) -> tuple[list[dict], dict]:
    """Parse the explicitly identified Appendix A; preserve every original cell."""
    root = ET.fromstring(xml_path.read_bytes())
    licenses = [' '.join(n.itertext()) for n in root.findall('.//license')]
    if not any('creativecommons.org/licenses/by/4.0/' in text for text in licenses):
        raise ValueError('Expected CC BY 4.0 license was not found')
    tables = root.findall('.//app//table')
    source_hash = file_digest(xml_path)
    if len(tables) != 1:
        raise ValueError('Appendix A table identification is ambiguous')
    table = tables[0]
    headers = [''.join(c.itertext()).strip() for c in table.findall('./thead/tr')[0]]
    if [''.join(h.split()) for h in headers] != ['No', 'γ(kN/m3)', 'c(kPa)', 'φ(°)', 'β(°)', 'H(m)', 'ru', 'Status']:
        raise ValueError('Unexpected Appendix A columns')
    rows = []
    for ordinal, row in enumerate(table.findall('./tbody/tr'), 1):
        cells = [''.join(c.itertext()).strip() for c in row]
        if len(cells) != 8 or cells[0] != str(ordinal):
            raise ValueError(f'Unexpected source row identity at {ordinal}')
        values = [float(v) for v in cells[1:7]]
        if not all(math.isfinite(v) for v in values):
            raise ValueError(f'Nonfinite public value at {ordinal}')
        weight, cohesion, friction, angle, height, ru = values
        if not (weight > 0 and cohesion >= 0 and 0 <= friction < 90
                and 0 < angle <= 90 and height > 0 and 0 <= ru <= 1):
            raise ValueError(f'Public value outside declared numeric domain at {ordinal}')
        if cells[7] not in {'Stable', 'Failure'}:
            raise ValueError(f'Unrecognized source label at {ordinal}')
        flags = []
        # Screening flags for source review, not engineering classification rules.
        if weight < 10:
            flags.append('unusual_unit_weight_in_published_unit_requires_source_review')
        if height < 1 or angle < 1:
            flags.append('small_geometry_requires_scale_and_unit_review')
        rows.append({'case_id': f'public627-{ordinal:04d}', 'source_type': 'public_literature_compilation',
                     'features': dict(zip(PUBLIC_FIELDS, values)),
                     'auxiliary_label': {'task': 'circular_mode_stability', 'value': cells[7],
                                         'origin': 'published_appendix_not_project_expert'},
                     'project_targets': {'review_priority': None, 'risk_level': None},
                     'raw_cells': cells, 'raw_cell_sha256': digest(cells),
                     'source': {'doi': '10.3390/s22239166', 'table': 'Appendix A',
                                'row': ordinal, 'file_sha256': source_hash},
                     'lineage_group': 'publication627_underlying_case_lineage_unknown',
                     'numeric_input_group': 'public-input-' + digest(values)[:16],
                     'split': 'auxiliary_development_unassigned', 'quality_flags': flags})
    if len(rows) != 627 or Counter(r['auxiliary_label']['value'] for r in rows) != Counter(Stable=311, Failure=316):
        raise ValueError('Published row count or class totals do not reconcile')
    groups = defaultdict(list)
    for row in rows:
        groups[row['numeric_input_group']].append(row)
    conflicts = []
    for group, members in groups.items():
        if len({r['auxiliary_label']['value'] for r in members}) > 1:
            conflicts.append({'group_id': group, 'rows': [r['source']['row'] for r in members],
                              'values': [r['auxiliary_label']['value'] for r in members],
                              'inputs': members[0]['features']})
            for row in members:
                row['quality_flags'].append('identical_inputs_conflicting_published_labels')
    # Candidate rounding/precision duplicates are kept, never silently collapsed.
    tolerances = (0.05, 0.1, 0.1, 0.1, 0.1, 0.005)
    near_pairs = []
    for i, left in enumerate(rows):
        a = list(left['features'].values())
        for right in rows[i + 1:]:
            b = list(right['features'].values())
            if a != b and all(abs(x - y) <= tol + 1e-12 for x, y, tol in zip(a, b, tolerances)):
                near_pairs.append([left['source']['row'], right['source']['row']])
    stats_table = root.find(".//table-wrap[@id='sensors-22-09166-t002']")
    if stats_table is None:
        raise ValueError('Published reconciliation table is missing')
    reconciliations = []
    for field, source_row in zip(PUBLIC_FIELDS, stats_table.findall('.//tbody/tr')):
        cells = [''.join(c.itertext()).strip() for c in source_row]
        values = [r['features'][field] for r in rows]
        published_min, published_max = map(float, cells[2].replace('–', '-').split('-'))
        calculated = [min(values), max(values), statistics.median(values),
                      statistics.mean(values), statistics.stdev(values)]
        published = [published_min, published_max, float(cells[3]), float(cells[4]), float(cells[5])]
        if any(abs(a - b) > 0.002 for a, b in zip(calculated, published)):
            raise ValueError(f'Public data failed statistics reconciliation: {field}')
        reconciliations.append({'field': field, 'calculated': calculated, 'published': published})
    for row in rows:
        row['quality_status'] = 'quarantined' if row['quality_flags'] else 'auxiliary_unreviewed'
    return rows, {'source_fidelity': '627 ordinals, all cells, class totals and Table 2 reconciled',
                  'row_count': len(rows), 'unique_numeric_inputs': len(groups),
                  'label_counts': dict(Counter(r['auxiliary_label']['value'] for r in rows)),
                  'conflicting_input_groups': conflicts,
                  'conflicting_label_records': sum(len(g['rows']) for g in conflicts),
                  'quarantined_records': sum(r['quality_status'] == 'quarantined' for r in rows),
                  'quality_flag_counts': dict(Counter(f for r in rows for f in r['quality_flags'])),
                  'near_duplicate_candidates': near_pairs,
                  'near_duplicate_tolerances': dict(zip(PUBLIC_FIELDS, tolerances)),
                  'near_duplicate_policy': 'review candidates only; not a physical equivalence rule',
                  'statistics': reconciliations, 'project_risk_training_eligible': 0,
                  'license': 'CC BY 4.0', 'underlying_case_lineage_verified': False}


def quantity(props: dict, prefix: str, unit: str) -> dict:
    lo, hi, raw = props.get(prefix + '_min'), props.get(prefix + '_max'), props.get(prefix + '_raw')
    for v in (lo, hi):
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)):
            raise ValueError('Invalid local geometry value')
    if lo is not None and hi is not None and lo > hi:
        raise ValueError('Reversed local geometry interval')
    if any(v is not None and (v <= 0 or unit == 'degree' and v > 90) for v in (lo, hi)):
        raise ValueError('Local geometry outside declared numeric domain')
    qualifier = ('upper_bound' if raw and any(c in raw for c in ['≤', '<=', '不大于']) else
                 'lower_bound' if raw and any(c in raw for c in ['≥', '>=', '不小于']) else
                 'reported_interval' if lo is not None and hi is not None and lo != hi else
                 'reported_value' if lo is not None or hi is not None else 'missing')
    return {'minimum': lo, 'maximum': hi, 'raw_text': raw, 'unit': unit, 'qualifier': qualifier,
            'field_source_status': 'unverified_field_locator', 'verified_evidence_ids': []}


def build_local_cases(graph: dict, graph_sha256: str) -> tuple[list[dict], list[dict], list[dict]]:
    nodes = {n['id']: n for n in graph['nodes']}
    evidence = {e['id']: e for e in graph['evidence']}
    if len(nodes) != len(graph['nodes']) or len(evidence) != len(graph['evidence']):
        raise ValueError('Duplicate graph or evidence IDs')
    slopes = sorted((n for n in graph['nodes'] if n['type'] == 'Slope'), key=lambda n: n['id'])
    cases, source_facts, prior_conclusions = [], [], []
    by_source = defaultdict(list)
    for edge in graph['edges']:
        if edge['source'] not in nodes or edge['target'] not in nodes:
            raise ValueError(f'Dangling graph edge {edge["id"]}')
        ref = edge.get('props', {}).get('evidence')
        if ref and ref not in evidence:
            raise ValueError(f'Unresolved evidence {ref}')
        by_source[edge['source']].append(edge)
    # Slopes sharing a document belong to the same engineering source group.
    parent = {n['id']: n['id'] for n in slopes}
    def find(x):
        while parent[x] != x:
            x = parent[x]
        return x
    owners = {}
    docs_by_slope = {}
    for slope in slopes:
        refs = {e.get('props', {}).get('evidence') for e in by_source[slope['id']]}
        refs.discard(None)
        docs = sorted({evidence[e]['document_id'] for e in refs})
        docs_by_slope[slope['id']] = docs
        for doc in docs:
            if doc in owners:
                parent[find(slope['id'])] = find(owners[doc])
            else:
                owners[doc] = slope['id']
    components = defaultdict(list)
    for slope in slopes:
        components[find(slope['id'])].append(slope['id'])
    for slope in slopes:
        props, sid = slope['props'], slope['id']
        refs = sorted({e['props']['evidence'] for e in by_source[sid] if e.get('props', {}).get('evidence')})
        fact_ids = []
        for edge in by_source[sid]:
            node = nodes[edge['target']]
            if node['type'] in MODEL_FACT_TYPES:
                fid = sid + ':' + edge['id']
                fact_ids.append(fid)
                source_facts.append({'fact_id': fid, 'case_id': 'local-' + sid,
                                     'relation': edge['relation'], 'node': deepcopy(node),
                                     'evidence_id': edge.get('props', {}).get('evidence'),
                                     'review_status': 'unreviewed', 'training_use': 'requires_field_and_role_review'})
            elif node['type'] in {'StabilityAnalysis', 'HazardSusceptibilityAssessment'}:
                prior_conclusions.append({'case_id': 'local-' + sid, 'node': deepcopy(node),
                                          'evidence_id': edge.get('props', {}).get('evidence'),
                                          'use': 'source_conclusion_reference_only_not_project_label_or_model_feature'})
        case = {'case_id': 'local-' + sid, 'schema_version': VERSION,
                'source_type': 'local_document_candidate', 'parent_case_id': None,
                'identity': {'graph_node_id': sid, 'source_slope_id': props.get('slope_id'),
                             'name': slope['label'], 'route': props.get('route_code_cache'),
                             'station_start_raw': props.get('start_station_raw'),
                             'station_end_raw': props.get('end_station_raw'), 'side': props.get('side'),
                             'scope': 'slope_entity_boundary_pending_expert_confirmation'},
                'input': {'height': quantity({'height_min': props.get('slope_height_min_m'),
                                              'height_max': props.get('slope_height_max_m'),
                                              'height_raw': props.get('slope_height_raw')}, 'height', 'm'),
                          'slope_angle': quantity({'angle_min': props.get('slope_gradient_min_deg'),
                                                   'angle_max': props.get('slope_gradient_max_deg'),
                                                   'angle_raw': props.get('slope_gradient_raw')}, 'angle', 'degree'),
                          'material_type': props.get('material_nature'),
                          'slope_type': props.get('slope_type'),
                          'slope_aspect_deg': props.get('slope_aspect_deg'),
                          'structure_relation': props.get('slope_structure_code'),
                          'fact_ids_for_review': fact_ids,
                          'assessment_as_of': None, 'condition': None,
                          'observed_drainage_condition': None,
                          'observed_deformation_condition': None,
                          'verified_protection_condition': None,
                          'consequence': {'road_impact': None, 'traffic_volume': None,
                                          'people_or_facilities': None, 'category': None},
                          'temporal_context': 'historical_document_date_and_current_status_unverified'},
                'provenance': {'graph_sha256': graph_sha256, 'document_ids': docs_by_slope[sid],
                               'context_evidence_ids': refs,
                               'context_evidence_role': 'slope_level_context_not_field_verification'},
                'source_group_id': 'engineering-' + digest(sorted(components[find(sid)]))[:16],
                'split': 'development_seen', 'targets': {'review_priority': None, 'risk_level': None},
                'quality': {'input_status': 'unreviewed', 'risk_label_status': 'unlabeled',
                            'flags': ['identity_scope_unreviewed', 'geometry_field_provenance_unverified',
                                      'assessment_date_condition_missing', 'consequence_missing',
                                      'historical_current_role_unverified']}}
        case['input_sha256'] = case_digest(case)
        cases.append(case)
    return cases, source_facts, prior_conclusions


def make_scenarios(cases: list[dict]) -> list[dict]:
    """Qualitative expert discussion scenarios, never a numerical solver claim."""
    selected = []
    for material, count in [('岩质', 4), ('土质', 2), ('土岩混合', 2)]:
        selected.extend([c for c in cases if c['input']['material_type'] == material][:count])
    changes = [('drainage', 'observed_drainage_condition', '假设排水堵塞'),
               ('deformation', 'observed_deformation_condition', '假设新出现变形迹象'),
               ('protection', 'verified_protection_condition', '假设防护设施出现损坏')]
    result = []
    for base in selected:
        for name, field, value in changes:
            row = deepcopy(base)
            row.update(case_id='scenario-' + base['case_id'] + '-' + name,
                       source_type='hypothetical_expert_scenario', parent_case_id=base['case_id'])
            row['input'][field] = value
            row['input']['temporal_context'] = 'hypothetical_noncalendar_scenario'
            row['input_sha256'] = case_digest(row)
            row['scenario'] = {'type': name, 'modified_fields': [field],
                               'changes': {field: {'before': base['input'][field], 'after': value}},
                               'generation_method': 'declared_qualitative_counterfactual',
                               'numerical_simulation_performed': False,
                               'generation_protocol_status': 'pending_domain_expert_review',
                               'is_observed_real_event': False}
            row['quality']['flags'].append('hypothetical_plausibility_and_generation_protocol_unreviewed')
            result.append(row)
    return result


def label_gate(case: dict, reviews: list[dict], scheme: dict, task: str) -> dict:
    """Check declared review records; does not authenticate human identities.

    An approved protocol and two independent expert records must concern the exact
    input version and assessment context. Disagreement requires recorded adjudication.
    """
    reasons = []
    if task not in {'review_priority', 'risk_level'}:
        raise ValueError('Unsupported project target')
    if case.get('input_sha256') != case_digest(case):
        reasons.append('input_digest_mismatch')
    if case.get('source_type') not in {'local_document_candidate', 'expert_verified_structured', 'hypothetical_expert_scenario'}:
        reasons.append('source_not_eligible_for_project_targets')
    if scheme.get('approval_status') != 'approved' or not all(scheme.get(k) for k in ['id', 'version', 'approved_by', 'approval_evidence']):
        reasons.append('scheme_not_approved')
    if scheme.get('task') != task:
        reasons.append('scheme_task_mismatch')
    try:
        date.fromisoformat(case['input']['assessment_as_of'])
    except (TypeError, ValueError, KeyError):
        reasons.append('assessment_date_missing_or_invalid')
    if not case['input'].get('condition'):
        reasons.append('assessment_condition_missing')
    if task == 'risk_level' and not any(v is not None for v in case['input'].get('consequence', {}).values()):
        reasons.append('consequence_missing')
    if case.get('source_type') == 'hypothetical_expert_scenario' and case.get('scenario', {}).get('generation_protocol_status') != 'approved':
        reasons.append('scenario_protocol_not_approved')
    valid = []
    reviewer_ids = set()
    allowed_values = set(scheme.get('levels', []))
    available_refs = set(case.get('provenance', {}).get('context_evidence_ids', []))
    for review in reviews:
        if review.get('case_id') != case['case_id'] or review.get('task') != task:
            continue
        if (review.get('input_sha256') != case['input_sha256']
                or review.get('scheme_id') != scheme.get('id')
                or review.get('scheme_version') != scheme.get('version')
                or review.get('assessment_as_of') != case['input'].get('assessment_as_of')
                or review.get('condition') != case['input'].get('condition')):
            reasons.append('stale_or_different_review_context')
            continue
        evidence_ids = set(review.get('evidence_ids', []))
        try:
            review_date = date.fromisoformat(review.get('reviewed_at'))
            assessment_date = date.fromisoformat(review.get('assessment_as_of'))
            dates_valid = review_date >= assessment_date
        except (ValueError, TypeError):
            dates_valid = False
        if (review.get('label_origin') != 'human_domain_expert'
                or review.get('input_verdict') != 'accepted'
                or review.get('field_verification_complete') is not True
                or review.get('independent_first_pass') is not True
                or review.get('reviewer_role') not in {'domain_expert', 'road_slope_expert', 'geotechnical_expert'}
                or review.get('label') not in allowed_values or not review.get('rationale')
                or not dates_valid or not review.get('reviewer_id')
                or not evidence_ids or not evidence_ids <= available_refs):
            reasons.append('incomplete_or_nonexpert_review')
            continue
        if review['reviewer_id'] in reviewer_ids:
            reasons.append('duplicate_reviewer')
            continue
        reviewer_ids.add(review['reviewer_id'])
        valid.append(review)
    if len(valid) < 2:
        reasons.append('two_independent_expert_reviews_required')
    if len({r['label'] for r in valid}) > 1:
        reasons.append('expert_disagreement_requires_adjudication')
    reasons = sorted(set(reasons))
    return {'eligible': not reasons, 'blocking_reasons': reasons,
            'label': valid[0]['label'] if not reasons else None,
            'independent_real_test_eligible': not reasons and case.get('source_type') in {'local_document_candidate', 'expert_verified_structured'}
                                               and case.get('split') == 'independent_real_test'}


def validate_cases(cases: list[dict], evidence_ids: set[str]) -> dict:
    ids, groups, issues = set(), defaultdict(set), []
    index = {r['case_id']: r for r in cases}
    for row in cases:
        if row['case_id'] in ids:
            issues.append('duplicate_case_id:' + row['case_id'])
        ids.add(row['case_id'])
        if row['input_sha256'] != case_digest(row):
            issues.append('input_digest_mismatch:' + row['case_id'])
        if any(v is not None for v in row['targets'].values()):
            issues.append('candidate_target_must_be_empty:' + row['case_id'])
        if not set(row['provenance']['context_evidence_ids']) <= evidence_ids:
            issues.append('unresolved_evidence:' + row['case_id'])
        groups[row['source_group_id']].add(row['split'])
        if row['source_type'] == 'hypothetical_expert_scenario':
            parent = index.get(row['parent_case_id'])
            if parent is None or parent['source_group_id'] != row['source_group_id'] or parent['split'] != row['split']:
                issues.append('scenario_parent_group_or_split_mismatch:' + row['case_id'])
            if row['split'] == 'independent_real_test':
                issues.append('synthetic_in_real_test:' + row['case_id'])
            if parent:
                changed = {k for k in row['input'] if row['input'][k] != parent['input'][k]}
                if changed != set(row['scenario']['modified_fields']) | {'temporal_context'}:
                    issues.append('undeclared_scenario_change:' + row['case_id'])
    for group, splits in groups.items():
        if len(splits) > 1:
            issues.append('source_group_crosses_splits:' + group)
    return {'status': 'passed' if not issues else 'failed', 'cases': len(cases),
            'source_groups': len(groups), 'issues': issues,
            'scope': 'format, source references, label separation and grouping; not engineering accuracy'}
