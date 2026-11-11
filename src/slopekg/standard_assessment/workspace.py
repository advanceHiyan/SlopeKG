"""Slope dossiers that link legacy evidence without modifying its case history."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import re
from urllib.parse import urlencode
from uuid import uuid4

from ..storage import read_json
from .adapters import PilotAdapter
from .contract import CONTRACT_VERSION, VersionConflict, identity
from .service import StandardAssessmentService
from .workspace_cases import SCENARIO_LABELS, compose_case, scenario_key, supported_case
from .workspace_sources import (
    ORIGIN_DATABASES, allowed_origin, archived_origin, document_names, legacy_url,
    origin_service, read_origin, source_subject, verified_context_pdf,
)


API_PREFIX = '/api/slope-assessment/'


def text_value(value, label, limit=500):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f'{label}不能为空或超过 {limit} 字')
    return value.strip()


def operator(request):
    return text_value(request.get('actor'), '实际操作者', 100), text_value(request.get('reason'), '依据说明')


def now():
    return datetime.now(timezone.utc).isoformat()


def case_link(slope_id, case_id=None):
    params = {'slope_id': slope_id}
    if case_id:
        params['case_id'] = case_id
    return '/web/slope-assessment.html?' + urlencode(params)


def result_summary(result):
    return {key: result.get(key) for key in ('result_id', 'resolution_version', 'table17_result',
            'workflow_status', 'review_status', 'basis_status', 'rule_version')} if result else None


class AssessmentWorkspace:
    def __init__(self, paths):
        self.paths = paths
        self.api = StandardAssessmentService(paths, database=paths.output_dir / 'assessment-workspace/assessment.sqlite')
        self.test_only = getattr(paths, 'assessment_test_only', False)
        with self.api.store.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS slope_dossier (
                id TEXT PRIMARY KEY, label TEXT NOT NULL, location TEXT NOT NULL,
                actor TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS slope_source_event (
                link_id TEXT PRIMARY KEY, workspace TEXT NOT NULL, case_id TEXT NOT NULL,
                subject_ref TEXT NOT NULL, slope_id TEXT NOT NULL, action TEXT NOT NULL,
                actor TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL)''')
            db.execute('''CREATE INDEX IF NOT EXISTS slope_source_case_index
                ON slope_source_event(workspace, case_id)''')

    def _graph_slopes(self):
        graph = read_json(self.paths.graph_json, {'nodes': []})
        return {row['id']: {'id': row['id'], 'label': row.get('label') or row['id'], 'kind': 'graph',
                'location': row.get('props', {}).get('source_alias', ''),
                'supported': '滑坡' in (row.get('summary') or '')}
                for row in graph['nodes'] if row.get('type') == 'Slope'}

    def _slopes(self, db):
        rows = db.execute('SELECT id,label,location,actor,reason,created_at FROM slope_dossier').fetchall()
        keys = ('id', 'label', 'location', 'actor', 'reason', 'created_at')
        return self._graph_slopes() | {r[0]: dict(zip(keys, r), kind='registered', supported=True) for r in rows}

    def _active_links(self, db):
        keys = ('link_id', 'workspace', 'case_id', 'subject_ref', 'slope_id', 'action', 'actor', 'reason', 'created_at')
        rows = db.execute('''SELECT e.link_id,e.workspace,e.case_id,e.subject_ref,e.slope_id,
                e.action,e.actor,e.reason,e.created_at FROM slope_source_event e
                JOIN (SELECT max(rowid) AS newest FROM slope_source_event GROUP BY workspace,case_id) latest
                ON e.rowid=latest.newest WHERE e.action='link' ''')
        return {(row[1], row[2]): dict(zip(keys, row)) for row in rows}

    def _implicit_link(self, workspace, case, slopes):
        target = case.get('target_ref')
        if workspace != 'main' or slopes.get(target, {}).get('kind') != 'graph':
            return None
        return {'link_id': identity('graphlink_', [target, case['case_id']]), 'slope_id': target,
                'workspace': workspace, 'case_id': case['case_id'], 'action': 'graph_source',
                'reason': '已有评估案例的图谱边坡标识'}

    def _summary(self, workspace, case, result, link):
        lineage = case.get('recognition_lineage') or {}
        slope_id = case.get('workspace_slope_id') if workspace == 'workspace' else link and link['slope_id']
        label = lineage.get('subject_label') or case.get('workspace_slope_label') or case['target_ref']
        names = document_names(case)
        archived = archived_origin(case) if workspace != 'workspace' else False
        scope = case['scope']
        scenario = scenario_key(scope)
        if scenario and scenario != scope['scenario'].get('code'):
            scope = dict(scope, scenario=dict(scope['scenario'], code=scenario))
        return {'workspace': workspace, 'case_id': case['case_id'], 'label': label, 'slope_id': slope_id,
                'scope': scope, 'document': names[0] if names else '档案记录', 'document_names': names,
                'created_at': case.get('created_at', ''), 'resolution_version': case['resolution_version'],
                'supported': supported_case(case), 'archived': archived, 'link': link,
                'source_count': len(case.get('workspace_sources', [])), 'result': result_summary(result),
                'detail_url': case_link(slope_id, case['case_id']) if workspace == 'workspace' else legacy_url(workspace, case['case_id'])}

    def _legacy_summaries(self, slopes, links):
        rows = []
        for workspace in ORIGIN_DATABASES:
            api = origin_service(self.paths, workspace)
            if api is None:
                continue
            with api.store.transaction() as db:
                cases = api.store.rows('assessment_case', db)
                results = self._current_results(api, cases, db)
            for case in cases:
                if allowed_origin(self.paths, case):
                    link = links.get((workspace, case['case_id'])) or self._implicit_link(workspace, case, slopes)
                    rows.append(self._summary(workspace, case, results.get(case['case_id']), link))
        return sorted(rows, key=lambda row: (row['archived'], row['label'], row['created_at']))

    @staticmethod
    def _current_results(api, cases, db):
        versions = {case['case_id']: case['resolution_version'] for case in cases}
        return {r['case_id']: r for r in api.store.rows('assessment_result', db)
                if versions.get(r['case_id']) == r['resolution_version'] and r['rule_snapshot_hash'] == api.rules_hash}

    def catalog(self):
        with self.api.store.transaction() as db:
            slopes, links = self._slopes(db), self._active_links(db)
            cases = self.api.store.rows('assessment_case', db)
        origins = self._legacy_summaries(slopes, links)
        counts = Counter(row['slope_id'] for row in origins if not row['archived'])
        history = Counter(c['workspace_slope_id'] for c in cases)
        rows = [dict(s, source_count=counts[s['id']], case_count=history[s['id']] + counts[s['id']])
                for s in slopes.values()]
        rows.sort(key=lambda r: (not bool(r['case_count']), not r['supported'], r['label']))
        return {'slopes': rows, 'source_cases': origins, 'test_only': self.test_only, 'scenario_options': SCENARIO_LABELS}

    def slope(self, slope_id):
        slope_id = text_value(slope_id, '边坡标识', 150)
        with self.api.store.transaction() as db:
            slopes, links = self._slopes(db), self._active_links(db)
            slope = slopes[slope_id]
            cases = [c for c in self.api.store.rows('assessment_case', db) if c['workspace_slope_id'] == slope_id]
            results = self._current_results(self.api, cases, db)
        sources = [row for row in self._legacy_summaries(slopes, links) if row['slope_id'] == slope_id]
        history = [self._summary('workspace', c, results.get(c['case_id']), None) for c in cases] + sources
        history.sort(key=lambda c: (c['created_at'], c['case_id']), reverse=True)
        return {'slope': slope, 'sources': sources, 'cases': history, 'test_only': self.test_only}

    def add_slope(self, request):
        label = text_value(request.get('label'), '边坡名称', 150)
        location = request.get('location', '')
        if not isinstance(location, str) or len(location.strip()) > 240:
            raise ValueError('位置说明需为不超过 240 字的文字')
        actor, reason = operator(request)
        with self.api.store.transaction() as db:
            normalized = re.sub(r'\s+', '', label).casefold()
            for slope in self._slopes(db).values():
                if re.sub(r'\s+', '', slope['label']).casefold() == normalized:
                    if slope.get('kind') == 'registered' and slope.get('location') == location.strip():
                        return {'slope': slope, 'reused': True}
                    raise VersionConflict('已有同名档案，请选择已有对象或用路线桩号区分')
            slope = {'id': 'dossier_' + uuid4().hex, 'label': label, 'location': location.strip(),
                     'actor': actor, 'reason': reason, 'created_at': now(), 'kind': 'registered', 'supported': True}
            db.execute('INSERT INTO slope_dossier VALUES (:id,:label,:location,:actor,:reason,:created_at)', slope)
        return {'slope': slope, 'reused': False}

    def _origin(self, workspace, case_id):
        api = origin_service(self.paths, workspace)
        if api is None:
            raise KeyError('来源评估尚未保存')
        data = read_origin(api, case_id)
        case = data['case']
        if archived_origin(case) or not supported_case(case):
            raise ValueError('只允许归档可评估的滑坡体来源。模拟档案和局部对象保留原处。')
        return data

    def link(self, request, *, unlink=False):
        slope_id = text_value(request.get('slope_id'), '边坡标识', 150)
        case_id = text_value(request.get('case_id'), '来源案例', 150)
        workspace = request.get('workspace')
        actor, reason = operator(request)
        if not unlink and request.get('same_object_confirmed') is not True:
            raise ValueError('请核对原件，并确认来源对象是本档案对应的同一个滑坡体')
        case = self._origin(workspace, case_id)['case']
        subject = source_subject(case)
        with self.api.store.transaction() as db:
            slopes, links = self._slopes(db), self._active_links(db)
            if slope_id not in slopes:
                raise KeyError('边坡档案不存在')
            implicit = self._implicit_link(workspace, case, slopes)
            if implicit:
                if slope_id != implicit['slope_id'] or unlink:
                    raise ValueError('已有图谱案例保留其原边坡归属')
                return implicit
            existing = links.get((workspace, case_id))
            if unlink:
                if not existing or existing['slope_id'] != slope_id:
                    raise VersionConflict('资料归属已变化，请刷新档案')
            else:
                if any(l['subject_ref'] == subject and l['slope_id'] != slope_id for l in links.values()):
                    raise VersionConflict('该来源对象已归入另一档案。请先核对并解除原归属。')
                if existing:
                    return existing
            event = {'link_id': 'slope_link_' + uuid4().hex, 'workspace': workspace, 'case_id': case_id,
                     'subject_ref': subject, 'slope_id': slope_id, 'action': 'unlink' if unlink else 'link',
                     'actor': actor, 'reason': reason, 'created_at': now()}
            db.execute('''INSERT INTO slope_source_event VALUES (:link_id,:workspace,:case_id,:subject_ref,
                :slope_id,:action,:actor,:reason,:created_at)''', event)
        return event

    def _graph_source(self, slope):
        curated = self.paths.root / '.private/knowledge/GBT40112-2021/assessment_v1/real_case_evidence.json'
        if self.test_only:
            curated = None
        data = PilotAdapter(self.paths, curated if curated and curated.is_file() else None).build(slope['id'])
        source_id = identity('graphsource_', [slope['id'], data['scope'], data['source_hashes']])
        case = {'case_id': source_id, 'target_ref': slope['id'], 'scope': data['scope'], 'resolution_version': 1,
                'source_hashes': data['source_hashes'], 'hazard_type_supported': data['hazard_type_supported']}
        return {'workspace': 'main', 'case': case, 'facts': data['facts'], 'contexts': data['contexts'],
                'source_records': data['source_records'], 'link': self._implicit_link('main', case, {slope['id']: slope}),
                'test_only': self.test_only}

    def create_case(self, request):
        actor, reason = operator(request)
        slope_id = text_value(request.get('slope_id'), '边坡标识', 150)
        sources = request.get('sources')
        if not isinstance(sources, list) or len(sources) > 30 or any(not isinstance(row, dict) for row in sources):
            raise ValueError('请选择不超过 30 条来源案例')
        with self.api.store.transaction() as db:
            slopes, links = self._slopes(db), self._active_links(db)
            slope = slopes[slope_id]
        bundles = []
        explicit_links = {}
        for row in sources:
            workspace = row.get('workspace')
            case_id = text_value(row.get('case_id'), '来源案例', 150)
            data = self._origin(workspace, case_id)
            case = data['case']
            if 'resolution_version' in row and row['resolution_version'] != case['resolution_version']:
                raise VersionConflict('来源案例已更新，请刷新后重新选择')
            implicit = self._implicit_link(workspace, case, slopes)
            link = implicit or links.get((workspace, case_id))
            if not link or link['slope_id'] != slope_id:
                raise ValueError('请先确认每份资料属于本边坡，再开始评估')
            if workspace == 'debug':
                for path, digest in case['source_hashes'].items():
                    if Path(path).suffix.lower() == '.pdf':
                        verified_context_pdf(self.paths, {'source_path': path, 'source_sha256': digest})
            if not implicit:
                explicit_links[workspace, case_id] = link['link_id']
            bundles.append(dict(data, workspace=workspace, link=link, test_only=self.test_only))
        if not bundles:
            if slope['kind'] != 'graph' or not slope['supported']:
                raise ValueError('请选择本边坡已经归档的滑坡来源资料')
            bundles = [self._graph_source(slope)]

        def guard(db):
            active = self._active_links(db)
            if any(active.get(key, {}).get('link_id') != link_id for key, link_id in explicit_links.items()):
                raise VersionConflict('资料归属已变化，请刷新后重试')

        result = compose_case(self.api, slope, bundles, anchor_case_id=request.get('anchor_case_id'),
                              selected_scenario=request.get('scenario'),
                              guard=guard, creation={'actor': actor, 'reason': reason})
        return self.decorate_case(result)

    def decorate_case(self, bundle):
        case = bundle['case']
        case_id = case['case_id']
        evidence = case.get('workspace_evidence', {})
        for ctx in bundle['contexts']:
            cid = ctx['context_id']
            source = ctx.get('source_path', '')
            name = Path(source).name
            lineage = ctx.get('recognition_context')
            if lineage:
                # Frozen history renames the internal PDF to source.pdf. Use
                # the original display name saved with the selected source.
                for row in case.get('workspace_sources', []):
                    source_lineage = row.get('recognition_lineage') or {}
                    if source_lineage.get('source_context') == lineage:
                        name = source_lineage.get('source_name') or name
                        break
            ctx['source_name'] = name
            is_pdf = Path(source).suffix.lower() == '.pdf' and bool(ctx.get('source_sha256'))
            ctx['usable_for_evidence'] = cid in evidence and is_pdf
            ctx['evidence_variables'] = evidence.get(cid, {}).get('variables', [])
            if is_pdf:
                params = {'case_id': case_id, 'context_id': cid}
                ctx['document_url'] = API_PREFIX + 'evidence?' + urlencode(params)
                for span in ctx.get('spans', []):
                    page = span.get('file_page_1_based')
                    if type(page) is int and page > 0:
                        span['page_url'] = API_PREFIX + 'evidence?' + urlencode(dict(params, page=page))
        bundle['test_only'] = self.test_only
        return bundle

    def case(self, case_id):
        bundle = self.api.get_case(text_value(case_id, '评估案例', 150))
        with self.api.store.transaction() as db:
            bundle['history_facts'] = [self.api.store.get('fact_assertion', fid, db) for fid in bundle['case']['fact_ids']]
            bundle['source_links_changed'] = self._links_changed(bundle['case'], db)
        return self.decorate_case(bundle)

    def _links_changed(self, case, db):
        links = self._active_links(db)
        return any(source.get('link', {}).get('action') == 'link'
                   and links.get((source['workspace'], source['case_id']), {}).get('link_id') != source['link_id']
                   for source in case.get('workspace_sources', []))

    def _require_current_links(self, case, db):
        if self._links_changed(case, db):
            raise VersionConflict('本次评估的资料归属已变更。请从边坡档案选择当前资料重新评估。')

    def evidence(self, case_id, context_id, page):
        with self.api.store.transaction() as db:
            case = self.api.store.get('assessment_case', case_id, db)
            if context_id not in case['context_ids']:
                raise KeyError('该证据不属于此评估案例')
            context = self.api.store.get('evidence_context', context_id, db)
        path = verified_context_pdf(self.paths, context)
        if page is None:
            return path.read_bytes(), 'application/pdf'
        if page not in {s.get('file_page_1_based') for s in context['spans']}:
            raise ValueError('该页不属于所选证据')
        import fitz
        with fitz.open(path) as pdf:
            if not 1 <= page <= len(pdf):
                raise ValueError('页码超出原件范围')
            source = pdf[page - 1]
            scale = min(3, 1600 / max(source.rect.width, source.rect.height))
            return source.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes('png'), 'image/png'

    def evaluate(self, request):
        case_id = text_value(request.get('case_id'), '评估案例', 150)
        args = self._args(request, case_id)
        with self.api.store.transaction() as db:
            self._require_current_links(self.api._case(case_id, args['expected_resolution_version'], db), db)
            self.api.evaluate_assessment(args, db=db)
        return self.case(case_id)

    def edit_evidence(self, request, *, correct=False):
        from .workspace_review import save_evidence

        result = save_evidence(self.api, request, correct=correct, guard=self._require_current_links)
        return self.case(result['case']['case_id']) | {'saved_change': result['saved_change']}

    @staticmethod
    def _args(request, case_id):
        return {'contract_version': CONTRACT_VERSION, 'case_id': case_id,
                'expected_resolution_version': request.get('expected_resolution_version')}

    def review(self, request, *, select=False):
        case_id = text_value(request.get('case_id'), '评估案例', 150)
        actor, reason = operator(request)
        args = self._args(request, case_id)
        if select:
            target = text_value(request.get('fact_id'), '来源事实', 150)
            decision = 'clear_selection' if request.get('clear') is True else 'select_branch'
        else:
            target = text_value(request.get('result_id'), '评估结果', 150)
            decision = 'confirm_result'
        with self.api.store.transaction() as db:
            case = self.api._case(case_id, args['expected_resolution_version'], db)
            self._require_current_links(case, db)
            if not select:
                current = self._current_results(self.api, [case], db).get(case_id)
                if not current or current['result_id'] != target:
                    raise VersionConflict('当前结果已变化，请刷新后再复核')
            if select and decision == 'select_branch':
                from .contract import applicability
                fact = next((f for f in self.api._facts(case, db) if f['fact_id'] == target), None)
                if not fact or applicability(fact, case)['status'] == 'incompatible' or fact['source_check']['content'] == 'unchecked':
                    raise ValueError('该来源的对象、时期、工况或证据未核定，不能直接采用')
            change = self.api.review_assessment({**args, 'decision': decision, 'target_id': target,
                'actor_kind': 'human', 'reviewer_ref': actor, 'reason': reason,
                'context_reviewed': request.get('context_reviewed')}, db=db)
            self.api.evaluate_assessment({**args, 'expected_resolution_version': change['case']['resolution_version']}, db=db)
        return self.case(case_id)
