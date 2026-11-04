"""Evidence-bound candidate normalization, independent of recognition prompts.

This module does not publish facts, decide professional development grades,
or accept a candidate. Callers must provide an explicit assessment scope and
source-page object inventory. It never reads a fixture catalog or answers.
"""
from __future__ import annotations

import copy
from decimal import Decimal, InvalidOperation
import re
import unicodedata

from .contract import enumeration, number

VERSION = 'knowledge-assessment-candidate-bridge-1'


def compact(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value))).replace('－', '-').replace('—', '-').replace('～', '~')


def source_dates(text):
    return list(dict.fromkeys(f'{int(y):04d}-{int(m):02d}-{int(d):02d}'
                             for y,m,d in re.findall(r'(\d{4})[-年](\d{1,2})[-月](\d{1,2})(?:日)?', text)))


def variable_for_field(field):
    field=compact(field)
    if '发育' in field and any(s in field for s in ['程度','状态','结论']):return 'development'
    if ('威胁' in field and any(s in field for s in ['人数','人口','人员数'])):return 'threatened_people'
    if ('潜在' in field or '可能' in field) and '损失' in field:return 'potential_loss_wan'
    if '直接' in field and '损失' in field:return 'direct_loss_wan'
    if '死亡' in field and ('人数' in field or '人员' in field):return 'deaths'
    if field.lower() in {'fs','稳定系数','计算稳定系数','计算稳定系数fs','稳定系数fs'}:return 'fs'
    if any(s in field for s in ['诱发因素','地质环境','岩性','水文条件']):return 'geological_context'
    return None


def normalize_value(raw):
    """Return a typed value or an explicit rejection; never infer missingness as 0."""
    variable=variable_for_field(raw.get('field',''))
    if variable is None:return {'accepted':False,'reason':'not_an_assessment_input'}
    text=compact(raw.get('value',''))
    quote=compact(' '.join(e.get('quote','') for e in raw.get('evidence',[])))
    if raw.get('status')=='rejected' or not quote:return {'accepted':False,'reason':'no_supported_source_quote'}
    if any(s in text for s in ['未知','未统计','未调查','尚未统计','未给出','不详','未记录']):
        return {'accepted':True,'variable':variable,'value':None,'transformations':['explicit_unknown_preserved']}
    if variable=='development':
        if text not in {'强发育','中等发育','弱发育'} or text not in quote:
            return {'accepted':False,'reason':'not_an_explicit_development_label'}
        if re.search(r'(不得|不能|并非|不等同|不能判为)[^。;；]{0,35}'+re.escape(text),quote):
            return {'accepted':False,'reason':'negated_or_prohibited_development_adoption'}
        return {'accepted':True,'variable':variable,'value':enumeration(variable,text),'transformations':[]}
    if variable=='geological_context':
        if text not in quote:return {'accepted':False,'reason':'context_not_literal'}
        return {'accepted':True,'variable':variable,'value':{'kind':'text','text':str(raw['value'])},'transformations':[]}
    unit=compact(raw.get('unit',''))
    if variable in {'threatened_people','deaths'}:
        if unit and unit not in {'人','person','persons'}:return {'accepted':False,'reason':'population_unit_is_not_people'}
        text=re.sub(r'(?:人|persons?)$','',text)
        canonical='person';factor=Decimal(1)
    elif variable.endswith('_loss_wan'):
        embedded=re.search(r'(亿元|万元|元)$',text)
        if embedded:
            if unit and unit!=embedded.group(1):return {'accepted':False,'reason':'conflicting_currency_units'}
            unit=embedded.group(1);text=text[:embedded.start()]
        factors={'元':Decimal('0.0001'),'万元':Decimal(1),'亿元':Decimal(10000),'10k_CNY':Decimal(1)}
        if unit not in factors:return {'accepted':False,'reason':'missing_or_unknown_currency_unit'}
        if unit not in quote and unit!='10k_CNY':return {'accepted':False,'reason':'currency_unit_not_in_quote'}
        canonical='10k_CNY';factor=factors[unit]
    else:
        if unit not in {'','1'}:return {'accepted':False,'reason':'coefficient_unit_mismatch'}
        canonical='1';factor=Decimal(1)
    if re.search(r'\d,\d',text):
        if not re.fullmatch(r'(?:\d{1,3}(?:,\d{3})+)(?:\.\d+)?',text):return {'accepted':False,'reason':'ambiguous_comma_number'}
        text=text.replace(',','')
    raw_numeric=text
    # Exact scalar and closed reported interval are supported. Approximate
    # scalars keep precision uncertainty; ranges are never replaced by a mean.
    approximate=text.startswith(('约','大约','近'))
    text=re.sub(r'^(大约|约|近)','',text)
    interval=re.fullmatch(r'(\d+(?:\.\d+)?)\s*[~至-]\s*(\d+(?:\.\d+)?)',text)
    try:
        if interval:
            lo,hi=[Decimal(v)*factor for v in interval.groups()]
            if lo>hi:return {'accepted':False,'reason':'reversed_interval'}
            value={'kind':'interval','lower':float(lo),'upper':float(hi),'lower_inclusive':True,'upper_inclusive':True,
                   'unit':canonical,'qualifier':'approximate' if approximate else 'reported_range','interval_semantics':'reported_range'}
        elif re.fullmatch(r'\d+(?:\.\d+)?',text):
            scalar=Decimal(text)*factor
            if variable in {'threatened_people','deaths'} and scalar!=scalar.to_integral():return {'accepted':False,'reason':'fractional_people'}
            value=number(float(scalar),canonical,'approximate' if approximate else 'exact_as_reported')
        else:return {'accepted':False,'reason':'not_a_scalar_or_reported_interval'}
    except InvalidOperation:return {'accepted':False,'reason':'invalid_numeric_value'}
    numeric_in_quote=re.search(r'(?<![\d.])'+re.escape(compact(raw_numeric).replace(',',''))+r'(?![\d.])',quote.replace(',','')) is not None
    if not numeric_in_quote:return {'accepted':False,'reason':'value_not_in_source_quote'}
    transforms=[]
    if factor!=1:transforms.append({'operation':'unit_conversion','raw_unit':unit,'canonical_unit':canonical,'factor':str(factor)})
    if interval:transforms.append('reported_interval_preserved')
    return {'accepted':True,'variable':variable,'value':value,'transformations':transforms}


def candidate(raw, *, case_scope, target_key, aliases, page_texts, page_targets):
    """Map a candidate, retaining incompatible roles/times instead of overriding them."""
    parsed=normalize_value(raw)
    if not parsed['accepted']:return parsed
    pages=sorted({e.get('page') for e in raw.get('evidence',[]) if e.get('page') in page_texts})
    if not pages:return {'accepted':False,'reason':'source_page_unavailable'}
    scope_text=compact(str(raw.get('scope',''))+' '+str(raw.get('object','')))
    quotes=' '.join(e.get('quote','') for e in raw.get('evidence',[]))
    target_matches=lambda t:any(compact(a) in compact(t) for a in aliases)
    direct=target_matches(scope_text) or target_matches(quotes)
    page_matches=[p for p in pages if page_targets.get(p,set())=={target_key}]
    # A generic concept can be linked only when its source evidence identifies
    # one explicitly requested object on the page. Multiple-body pages abstain.
    if not direct and not page_matches:return {'accepted':False,'reason':'ambiguous_or_unbound_source_object'}
    if not direct and len(page_matches)!=len(pages):return {'accepted':False,'reason':'mixed_object_evidence_pages'}
    page_text='\n'.join(page_texts[p] for p in pages)
    role=raw.get('role','unknown')
    out_scope=copy.deepcopy(case_scope)
    if re.search(r'(?:[IⅠⅡⅢ一二三四A-Z0-9]+区|断面|剖面|局部)',str(raw.get('scope',''))):
        out_scope['subject']['source_object_ref']=case_scope['subject']['source_object_ref']+'/source-subscope/'+compact(raw['scope'])
        out_scope['subject']['object_kind']='source_subscope'
    dates=source_dates(str(raw.get('time','')))
    if not dates:
        dates=source_dates(quotes)
    if not dates:
        survey=re.findall(r'调查日\s*[：:]\s*(\d{4}-\d{2}-\d{2})',page_text)
        dates=list(dict.fromkeys(survey))
    if len(dates)==1:
        out_scope['time']={'kind':'instant','raw':dates[0],'start':dates[0],'end':dates[0]}
    else:
        out_scope['time']={'kind':'unknown','raw':raw.get('time') or '源资料未确定事实时期','start':None,'end':None}
    cond=compact(str(raw.get('condition','')))
    if '暴雨' in cond:
        out_scope['scenario']={'code':'rainstorm','raw':str(raw.get('condition')),'basis_refs':[]}
    elif '天然' in cond or '天然工况' in compact(page_text[:650]):
        out_scope['scenario']=copy.deepcopy(case_scope['scenario'])
    else:
        out_scope['scenario']={'code':'unspecified','raw':raw.get('condition') or '未明确','basis_refs':[]}
    # Explicit future scenarios stay predictions even when metadata omits it.
    # Body prose may explicitly mention a following prediction chapter. Only
    # page headings can establish a page-level future-scenario classification.
    headings='\n'.join('\n'.join(page_texts[p].splitlines()[:3]) for p in pages)
    if re.search(r'预测假设|暴雨预测情景',headings):role='prediction'
    standard='GB/T 40112-2021' if re.search(r'GB/?T\s*40112',page_text+str(raw.get('field',''))) else 'unknown'
    if re.search(r'采用标准.{0,8}DZ/?T',page_text):standard='other_standard_or_mixed'
    basis='conditional' if not direct or raw.get('status')!='source_aligned' or any(e.get('source')=='ocr' for e in raw.get('evidence',[])) else 'supported'
    transformations=list(parsed['transformations'])
    # A currently reported potential consequence is an assessment estimate,
    # distinct from a future hypothetical scenario. Keep the original role and
    # the conditional reinterpretation in the audit trail; no professional
    # acceptance is implied. Explicit future assumptions never take this path.
    if (parsed['variable']=='potential_loss_wan' and role=='prediction'
            and out_scope['time']==case_scope['time'] and out_scope['scenario']==case_scope['scenario']
            and re.search(r'险情估计|本期潜在|现状潜在',quotes)
            and not re.search(r'若|假设|预测情景',quotes+headings)):
        role='reported_estimate';basis='conditional'
        transformations.append('current_potential_consequence_estimate_requires_review')
    return {**parsed,'scope':out_scope,'role':role,'source_role':raw.get('role','unknown'),
            'source_standard':standard,'basis_status':basis,
            'binding_basis':'explicit_fact_subject' if direct else 'unique_explicit_object_on_source_page',
            'transformations':transformations,'source_pages':pages}


def explicit_judgment_candidates(units):
    """Retrieve literal professional conclusions overlooked by recognition.

    No qualitative feature, Fs, slope angle or table4 characteristic is turned
    into a grade. This supports only an existing, explicitly written conclusion.
    """
    rows=[]
    for unit in units:
        text=unit.get('text','')
        match=re.search(r'发育程度(?:专业)?结论\s*(?:为|[：:])\s*(强发育|中等发育|弱发育)',text)
        if not match or re.search(r'不得|不能|未给出|没有形成|不等同',text):continue
        rows.append({'id':'literal_judgment_'+unit['id'],'field':'发育程度','value':match.group(1),'unit':'',
                     'role':'observation','object':'','scope':'','time':'','condition':'','status':'source_aligned',
                     'extraction_origin':'explicit_source_statement_retrieval',
                     'evidence':[{'span_id':unit['id'],'quote':text,'page':unit['page'],'source':unit.get('source','unknown')}]})
    return rows
