"""Save a scoped evidence supplement or transcription correction with its result."""
from __future__ import annotations

from decimal import Decimal
import re
import uuid

import fitz

from .contract import CONTRACT_VERSION, UNITS, VOCABS, fact_record, identity, literal
from .knowledge_bridge import compact
from .service import slot, timestamp
from .workspace_cases import REFERENCE_ROLES, period_key, scenario_key


GB_STANDARD = 'GB/T 40112-2021'
TEXT_FIELDS = {'observations', 'geological_context'}
ENUM_FIELDS = set(VOCABS) - {'hazard_level'}
EDITABLE_FIELDS = set(UNITS) | ENUM_FIELDS | TEXT_FIELDS

# The number must be tied to the right quantity. A death count, for example,
# cannot be used as the threatened population just because both use 人.
_LABELS = re.compile('|'.join(f'(?P<{variable}>{pattern})' for variable, pattern in {
    'threatened_people': r'(?:受)?威胁(?:人数|人口(?:数)?|人员(?:数|数量)?)',
    'deaths': r'死亡(?:人数|人员(?:数)?)?|遇难(?:人数)?',
    'potential_loss_wan': r'(?:潜在|可能)(?:直接)?(?:经济)?损失',
    'direct_loss_wan': r'(?:实际)?直接(?:经济)?损失',
    'body_mean_angle': r'(?:(?:滑坡体|滑体)(?:的)?)?平均(?:坡角|坡度|倾角)',
    'fst': r'(?:目标|要求)?安全系数(?:Fst)?|(?<![A-Za-z])Fst(?![A-Za-z])',
    'fs': r'(?:计算)?稳定系数(?:Fs)?|(?<![A-Za-z])Fs(?![A-Za-z])',
    'development': r'发育程度(?:专业)?(?:结论|等级)?',
    'harm': r'危害程度(?:专业)?(?:结论|等级)?',
    'assessment_basis': r'评估(?:依据|口径|类别)',
}.items()), re.IGNORECASE)
_NUMBERS = re.compile(
    r'(?<![\dA-Za-z.,+\-−–‐‑])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\dA-Za-z]|[.,]\d)'
)
_UNITS_IN_TEXT = re.compile(r'^(亿元|万元|元|人|°|度)')
_BOUND_BEFORE = re.compile(r'(?:[<>≤≥=±~到至]|不?[少低多高]于|至少|至多|大于|小于|超过|不足|最多|最少)$')
_BOUND_AFTER = re.compile(r'^(?:[<>≤≥±~%/]|[到至\-−–‐‑]\d|以上|以下|以内|以外|起|余|次)')
_APPROX_BEFORE = re.compile(r'(?:大约|约|接近|近|≈)$')
_APPROX_AFTER = re.compile(r'^(?:左右|上下)')
_NEGATED = re.compile(r'不得|不能|不应|不宜|未(?:达到|判为|形成|统计)|并非|不是|不属于|不等同|非$|否认')
_REFERENCE_STATEMENT = re.compile(r'假设|预测情景|历史对照|前次|上期|修订前|举例')
_QUANTITY_END = re.compile(r'\d(?:亿元|万元|元|人|°|度)?$')


def _required_text(request, key, label, limit):
    value = request.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'请填写{label}，不超过{limit}字')
    return value.strip()


def _scope_key(scope):
    subject = scope['subject']
    period, scenario = period_key(scope), scenario_key(scope)
    if not subject.get('source_object_ref') or period is None or scenario is None:
        return None
    return (subject['source_object_ref'], subject.get('object_kind'), subject.get('spatial_extent_ref'),
            period, scenario)


def _unit_factor(variable, unit):
    canonical = UNITS[variable]
    if canonical == '10k_CNY':
        return {'元': Decimal('0.0001'), '万元': Decimal(1), '亿元': Decimal(10000)}.get(unit)
    if unit in {'person': {'人'}, 'deg': {'°', '度'}, '1': {''}}[canonical]:
        return Decimal(1)
    return None


def _numeric_literal(variable, value, clause, sole_variable):
    labels = list(_LABELS.finditer(clause))
    expected = Decimal(str(value['value']))
    for match in _NUMBERS.finditer(clause):
        prefix, suffix = clause[:match.start()], clause[match.end():]
        label = next((item for item in reversed(labels) if item.end() <= match.start()), None)
        if label and (label.lastgroup != variable or match.start() - label.end() > 45):
            continue
        if not label and (not sole_variable or labels):
            continue
        local_prefix = prefix[label.end():] if label else prefix
        bound_prefix = _APPROX_BEFORE.sub('', local_prefix).rstrip(':=')
        if _NEGATED.search(local_prefix) or _BOUND_BEFORE.search(bound_prefix):
            continue
        shown_unit = _UNITS_IN_TEXT.match(suffix)
        unit = shown_unit[1] if shown_unit else ''
        tail = suffix[shown_unit.end():] if shown_unit else suffix
        # Accept a unit printed in the column header immediately before a cell.
        if not unit:
            header_unit = re.search(r'\((?:单位:)?(亿元|万元|元|人|°|度)\)[:=]?$', local_prefix)
            if header_unit:
                unit = header_unit[1]
        factor = _unit_factor(variable, unit)
        if factor is None or _BOUND_AFTER.match(tail):
            continue
        approximate = bool(_APPROX_BEFORE.search(local_prefix) or _APPROX_AFTER.match(tail))
        if approximate != (value['qualifier'] == 'approximate'):
            continue
        if Decimal(match[0].replace(',', '')) * factor == expected:
            return True
    return False


def _quote_supports(variable, value, quote, variables):
    normalized = compact(quote)
    if variable in TEXT_FIELDS:
        return bool(compact(value['text'])) and compact(value['text']) in normalized
    sole_variable = set(variables) == {variable}
    for raw_clause in re.split(r'[。；;！!？?\r\n]', quote):
        clause = compact(raw_clause)
        if _REFERENCE_STATEMENT.search(clause):
            continue
        if variable in UNITS and _numeric_literal(variable, value, clause, sole_variable):
            return True
        if variable in ENUM_FIELDS:
            labels = [label for label in VOCABS[variable][1] if label in clause]
            if labels != [value['label']]:
                continue
            prefix = clause[:clause.find(value['label'])]
            if not _NEGATED.search(prefix) and (sole_variable or any(
                    match.lastgroup == variable for match in _LABELS.finditer(prefix))):
                return True
    return False


def _native_quote_matches(page_text, quote, value):
    text = compact(quote)
    for match in re.finditer(re.escape(text), page_text):
        before, after = page_text[:match.start()], page_text[match.end():]
        if value['kind'] == 'number':
            if text[0].isdigit() and (re.search(r'[\d.,+\-−–‐‑]$', before)
                                     or _BOUND_BEFORE.search(before) or _APPROX_BEFORE.search(before)):
                continue
            if _QUANTITY_END.search(text) and (_BOUND_AFTER.match(after) or _APPROX_AFTER.match(after)
                    or text[-1].isdigit() and re.match(r'^\d|^[.,]\d', after)):
                continue
        return True
    return False


def _quote_proof(api, case, request, variable, value, db):
    from .workspace_sources import verified_context_pdf

    context_id = _required_text(request, 'evidence_id', '原文证据', 160)
    entry = case.get('workspace_evidence', {}).get(context_id)
    if (context_id not in case['context_ids'] or not entry or _scope_key(entry['scope']) != _scope_key(case['scope'])
            or REFERENCE_ROLES.intersection(entry['roles'])):
        raise ValueError('原文证据须已关联本边坡、同期、同工况并可用于本案例')
    if request.get('source_page_reviewed') is not True:
        raise ValueError('请打开原页并确认已核对该条引文')
    context = api.store.get('evidence_context', context_id, db)
    spans = context['spans']
    index = request.get('evidence_span_index')
    if index is not None and (type(index) is not int or not 0 <= index < len(spans)):
        raise ValueError('所选原文片段不存在')
    choices = range(len(spans)) if index is None else [index]
    supported = [i for i in choices if _quote_supports(variable, value, spans[i].get('quote', ''), entry['variables'])]
    if len(supported) != 1:
        if len(supported) > 1:
            raise ValueError('有多段引文匹配，请选定要引用的原文片段')
        raise ValueError('所选原文未明确支持此指标、数值、精度或单位')
    index = supported[0]
    span = spans[index]
    page_number = span.get('file_page_1_based')
    path = verified_context_pdf(api.paths, context)
    with fitz.open(path) as pdf:
        if type(page_number) is not int or not 1 <= page_number <= len(pdf):
            raise ValueError('证据缺少有效的PDF页码')
        page = pdf[page_number - 1]
        page_text = compact(page.get_text())
        if page_text and not _native_quote_matches(page_text, span['quote'], value):
            raise ValueError('保存的引文与该PDF页的文字层不一致，请核对原始资料')
        if not page_text and not page.get_images() and not page.get_drawings():
            raise ValueError('证据所在PDF页没有可供核对的内容')
    proof = {'context_id': context_id, 'span_index': index, 'file_page_1_based': page_number,
             'source_path': context['source_path'], 'source_sha256': context['source_sha256'],
             'quote': span['quote'], 'page_check': 'native_text_matched' if page_text else 'human_visual_review'}
    standard = GB_STANDARD if re.search(r'GB/T40112-2021(?!\d)', page_text or compact(span['quote']), re.IGNORECASE) else 'unknown'
    return proof, standard


def _typed_value(variable, request):
    value = request.get('value')
    kind = 'number' if variable in UNITS else 'enum' if variable in ENUM_FIELDS else 'text'
    if not isinstance(value, dict) or value.get('kind') != kind:
        raise ValueError('补证或纠错需提交此指标对应的标量、枚举或原文文字')
    if kind == 'number':
        qualifier = value.get('qualifier', 'exact_as_reported')
        if qualifier not in ('exact_as_reported', 'approximate'):
            raise ValueError('请保留原文的精确或约数口径')
        return {'kind': kind, 'value': value.get('value'), 'unit': value.get('unit'), 'qualifier': qualifier}
    if kind == 'enum':
        return {key: value.get(key) for key in ('kind', 'code', 'label', 'vocabulary')}
    return {'kind': kind, 'text': _required_text(value, 'text', '原文文字或现场记录', 4000)}


def save_evidence(api, request, *, correct=False, guard=None):
    """Apply one current-case edit, keeping the cited original and prior versions."""
    case_id = _required_text(request, 'case_id', '评估案例', 160)
    actor = _required_text(request, 'actor', '经办人', 120)
    reason = _required_text(request, 'reason', '依据及说明', 2000)
    mode = request.get('mode', 'source_quote')
    if mode not in ('source_quote', 'field_observation') or correct and mode != 'source_quote':
        raise ValueError('纠错需使用有原页的原文证据；现场观察仅用于补充事实')

    with api.store.transaction() as db:
        case = api._case(case_id, request.get('expected_resolution_version'), db)
        if guard is not None:
            guard(case, db)
        if not case.get('workspace_slope_id') or not case.get('hazard_type_supported') or not _scope_key(case['scope']):
            raise ValueError('请先在滑坡工作台确定本次评估对象、时期和工况')
        active = api._facts(case, db)
        target = None
        if correct:
            target = next((fact for fact in active if fact['fact_id'] == request.get('fact_id')), None)
            if (not target or target.get('derivation_id') or target['role'] in REFERENCE_ROLES
                    or _scope_key(target['scope']) != _scope_key(case['scope'])):
                raise ValueError('只能纠正本案例当前版本的同范围原始输入')
        variable = target['variable_code'] if target else request.get('variable')
        if not isinstance(variable, str) or variable not in EDITABLE_FIELDS or target and request.get('variable', variable) != variable:
            raise ValueError('只能补充评估输入或纠正原输入指标，等级由规则计算')
        value = _typed_value(variable, request)
        # Reuse an equivalent representation to avoid treating 18 and 18.0
        # as conflicting reports of the same scalar.
        value = next((fact['value'] for fact in active if fact['variable_code'] == variable and fact['value'] == value), value)
        if target:
            role, semantic, feature = target['role'], target['semantic_kind'], target.get('feature_code')
        else:
            role, semantic, feature = 'observation', 'observation', None
            if variable in ENUM_FIELDS:
                role, semantic = 'judgment', 'reported_judgment'
            elif variable in {'fs', 'fst'}:
                role, semantic = 'calculation', 'reported_calculation'
            elif variable in TEXT_FIELDS:
                feature = 'operator_observation'
        source_ref = 'source_' + uuid.uuid4().hex
        # Validate the typed input with the common assessment contract before
        # opening a source PDF or writing any records.
        try:
            fact = fact_record(variable, value, target['scope'] if target else case['scope'],
                               source_refs=[source_ref] + (target['source_record_refs'] if target else []),
                               evidence_refs=[], origin='manual', role=role,
                               semantic=semantic, feature=feature, supersedes=target['fact_id'] if target else None)
        except OverflowError as exc:
            raise ValueError('数值超出支持范围') from exc
        if variable == 'fst' and literal(value) <= 1:
            raise ValueError('Fst须大于1，才能作为本流程的比较阈值')

        proof = None
        standard = 'unknown'
        if mode == 'source_quote':
            proof, standard = _quote_proof(api, case, request, variable, value, db)
        elif request.get('evidence_id') or variable in {'fs', 'fst'}:
            raise ValueError('现场观察不引用未核对的原文，稳定系数须引用计算资料')
        supplied_standard = request.get('source_standard')
        if supplied_standard not in (None, '', 'unknown', standard):
            raise ValueError('原页未支持该标准编号，请保留标准未确认')
        # A visual check can ground the operator's transcription without
        # presenting it as an automatic verification of scanned text.
        checked = 'literal_matched' if proof and proof['page_check'] == 'native_text_matched' else 'human_supported'
        standard_unconfirmed = variable in {'development', 'harm'} and standard != GB_STANDARD
        if standard_unconfirmed:
            checked = 'unchecked'
        fact.update(origin_kind='manual_transcription' if proof else 'manual', source_standard=standard,
                    evidence_refs=[proof['context_id']] if proof else [], source_check={
                        'locator': 'located' if proof else 'unlocated', 'content': checked})
        if checked != 'literal_matched':
            fact['basis_status'] = 'conditional'
        fact['fact_id'] = identity('fact_', {key: val for key, val in fact.items() if key != 'fact_id'})
        receipt = {'review_id': 'review_' + uuid.uuid4().hex, 'case_id': case['case_id'],
                   'target_id': target['fact_id'] if target else None, 'target_version': case['resolution_version'],
                   'accepted_resolution_version': case['resolution_version'] + 1,
                   'decision': 'correct' if target else 'supplement', 'actor_kind': 'human',
                   'reviewer_ref': actor, 'reason': reason, 'reviewed_at': timestamp(),
                   'professional_acceptance': False, 'variable': variable, 'source_mode': mode,
                   'evidence': proof, 'basis_refs': fact['evidence_refs'],
                   'adoption_status': 'standard_unconfirmed' if standard_unconfirmed else 'candidate_input',
                   'previous_value': target['value'] if target else None, 'replacement_value': value,
                   'replacement_fact_id': fact['fact_id']}
        source = {'record_ref_id': source_ref, 'source_path': 'operator_record', 'snapshot_sha256': 'in_database',
                  'collection': 'workspace_evidence_edit', 'record_id': receipt['review_id'], 'original_record': receipt}
        api.store.put('source_record', source, db)
        api.store.put('fact_assertion', fact, db)
        api.store.put('review_decision', receipt, db)
        case['fact_ids'].append(fact['fact_id'])
        # Reconsider this field's previous selection. A new contradictory
        # observation must remain visible rather than inherit a stale choice.
        case['selections'].pop(slot(fact), None)
        case.setdefault('selection_actor', {}).pop(slot(fact), None)
        case['resolution_version'] += 1
        api.store.update_case(case, db)
        api.evaluate_assessment({'contract_version': CONTRACT_VERSION, 'case_id': case['case_id'],
                                 'expected_resolution_version': case['resolution_version']}, db=db)
    return {**api.get_case(case['case_id']), 'saved_change': receipt}
