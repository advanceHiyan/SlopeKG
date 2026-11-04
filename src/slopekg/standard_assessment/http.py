"""Small additional routes; old review-priority endpoints retain their meaning."""
from __future__ import annotations

from urllib.parse import parse_qs

from .contract import CONTRACT_VERSION, VersionConflict
from .service import StandardAssessmentService

PREFIX = '/api/standard-assessment'


def service(paths,workspace='main'):
    if workspace not in {'main','debug'}:raise ValueError('无效评估工作区')
    citations = paths.root/'.private/knowledge/GBT40112-2021/assessment_v1/real_case_evidence.json'
    return StandardAssessmentService(paths, curated_evidence=citations if citations.is_file() else None,
        database=paths.output_dir/'assessment-debug/assessment.sqlite' if workspace=='debug' else None)


def get_route(handler, parsed, paths) -> bool:
    if parsed.path not in {PREFIX+'/contract', PREFIX+'/case', PREFIX+'/targets'}:
        return False
    if parsed.path.endswith('/targets'):
        handler.write_json({'targets':service(paths).targets()})
        return True
    if parsed.path.endswith('/contract'):
        handler.write_json({'contract_version':CONTRACT_VERSION,'status':'bounded_pilot',
            'operations':['run','prepare','retrieve','judge','assertion','context','evaluate','review','case'],
            'database':'output/demo/assessment-pilot/assessment.sqlite',
            'supported':['existing_basic_graph','selected_knowledge_import_via_local_service','explicit_curated_source_quotes',
                         'table4_coefficient_axis','table15','table17','source_correction_and_versioning'],
            'judgment':'single_configured_model_evidence_proposal_human_review',
            'not_implemented':['stability_solver','population_estimation','automatic_cross_document_identity'],
            'formal_assessment_enabled':False})
        return True
    try:
        query = parse_qs(parsed.query)
        workspace=query.get('workspace',['main'])[0]
        if workspace not in {'main','debug'}:raise ValueError('无效评估工作区')
        if not (paths.output_dir/('assessment-debug' if workspace=='debug' else 'assessment-pilot')/'assessment.sqlite').is_file():
            raise KeyError('pilot database not prepared')
        handler.write_json(service(paths,workspace).get_case(query.get('case_id',[''])[0]))
    except KeyError as exc:
        handler.write_json({'error':str(exc)},status=404)
    except ValueError as exc:handler.write_json({'error':str(exc)},status=400)
    return True


def post_route(handler, parsed, paths) -> bool:
    methods = {'prepare':'prepare_assessment','context':'get_fact_context','evaluate':'evaluate_assessment','review':'review_assessment',
               'run':'run_assessment','retrieve':'retrieve_assessment','judge':'judge_assessment','assertion':'add_assertion'}
    operation = parsed.path.removeprefix(PREFIX+'/')
    if not parsed.path.startswith(PREFIX+'/') or operation not in methods:
        return False
    try:
        payload = handler.read_json_body()
        if not isinstance(payload,dict):raise ValueError('请求必须是JSON对象')
        workspace=payload.get('workspace','main')
        if workspace=='debug' and operation in {'run','prepare'}:raise ValueError('调试案例请从明确的识别历史衔接创建')
        if workspace=='debug' and operation=='judge' and payload.get('allow_model') is not True:
            raise ValueError('模型辅助需明确允许发送本案例相关片段')
        handler.write_json(getattr(service(paths,workspace),methods[operation])(payload))
    except VersionConflict as exc:
        handler.write_json({'error':str(exc),'code':'version_conflict'},status=409)
    except KeyError as exc:
        handler.write_json({'error':str(exc),'code':'record_not_found'},status=404)
    except (TypeError,ValueError) as exc:
        handler.write_json({'error':str(exc),'code':'invalid_request'},status=400)
    except OSError as exc:
        handler.write_json({'error':str(exc),'code':'source_unavailable'},status=409)
    return True
