"""Read existing assessment snapshots and resolve their frozen original pages."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from ..debug_history import checked_artifact
from ..recognition_debug import DENIED_HASHES, _safe_location
from ..storage import verify_file_sha256
from .service import StandardAssessmentService


ORIGIN_DATABASES = {'main': 'assessment-pilot', 'debug': 'assessment-debug'}
# A known software demonstration remains readable in its original task. It is
# not selectable evidence for an operational slope.
ARCHIVED_AGENT_RUNS = {'agent-20261107-134803-ae7eabd8'}


def origin_service(paths, workspace):
    if not isinstance(workspace, str) or workspace not in ORIGIN_DATABASES:
        raise ValueError('请选择已有的评估来源')
    database = paths.output_dir / ORIGIN_DATABASES[workspace] / 'assessment.sqlite'
    return StandardAssessmentService(paths, database=database) if database.is_file() else None


def allowed_origin(paths, case):
    """A legacy database entry cannot grant access to restricted sources."""
    lineage = case.get('recognition_lineage') or {}
    if lineage.get('source_sha256') in DENIED_HASHES:
        return False
    for path, digest in case.get('source_hashes', {}).items():
        if digest in DENIED_HASHES:
            return False
        try:
            _safe_location(path, paths.root)
        except (ValueError, OSError):
            return False
    return True


def archived_origin(case):
    lineage = case.get('recognition_lineage') or {}
    return bool(case.get('archived') or case.get('test_only') or lineage.get('agent_run_id') in ARCHIVED_AGENT_RUNS)


def source_subject(case):
    return case['scope']['subject'].get('source_object_ref')


def legacy_url(workspace, case_id):
    if workspace == 'debug':
        return '/web/assessment-debug.html?' + urlencode({'case': case_id})
    return '/web/risk.html#' + urlencode({'case': case_id})


def read_origin(api, case_id):
    """Read one version and its original references in a single transaction."""
    with api.store.transaction() as db:
        case = api.store.get('assessment_case', case_id, db)
        if not allowed_origin(api.paths, case):
            raise KeyError('评估资料不在开放范围')
        facts = api._facts(case, db)
        contexts = [api.store.get('evidence_context', cid, db) for cid in case['context_ids']]
        for context in contexts:
            if not allowed_origin(api.paths, {'source_hashes': {context['source_path']: context.get('source_sha256')}}):
                raise KeyError('原文证据不在开放范围')
        refs = sorted({ref for fact in facts for ref in fact['source_record_refs']})
        return {'case': case, 'facts': facts, 'contexts': contexts,
                'source_records': [api.store.get('source_record', ref, db) for ref in refs]}


def verified_context_pdf(paths, context):
    digest = context.get('source_sha256')
    source = context.get('source_path')
    if not isinstance(source, str) or not source or not isinstance(digest, str) or digest in DENIED_HASHES:
        raise ValueError('该证据没有可核对的开放原件')
    path = _safe_location(source, paths.root)
    if path.suffix.lower() != '.pdf' or not path.is_file():
        raise ValueError('未找到本条证据对应的 PDF 原件')
    allowed_roots = (paths.raw_dir.resolve(), (paths.output_dir / 'debug-history').resolve())
    if not any(path.is_relative_to(root) for root in allowed_roots):
        raise ValueError('原件不属于已上传资料或冻结识别快照')
    checked_artifact(path)
    verify_file_sha256(path, digest)
    return path


def document_names(case):
    lineage = case.get('recognition_lineage') or {}
    if lineage.get('source_name'):
        return [lineage['source_name']]
    if case.get('workspace_sources'):
        return list(dict.fromkeys(name for source in case['workspace_sources'] for name in document_names(source)))
    return list(dict.fromkeys(Path(p).name for p in case.get('source_hashes', {}) if Path(p).suffix.lower() == '.pdf'))
