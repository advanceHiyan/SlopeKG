"""HTTP entry point shared by the dossier and recognition result pages."""
from http import HTTPStatus
from urllib.parse import parse_qs

from .contract import VersionConflict
from .workspace import API_PREFIX, AssessmentWorkspace


GET_OPERATIONS = {'catalog', 'slope', 'case', 'evidence'}
POST_OPERATIONS = {'slopes', 'link', 'unlink', 'create', 'evaluate', 'supplement', 'correct', 'review', 'select'}


def operation(parsed, allowed):
    if not parsed.path.startswith(API_PREFIX):
        return None
    name = parsed.path.removeprefix(API_PREFIX)
    return name if name in allowed else None


def handle_error(handler, exc):
    if isinstance(exc, KeyError):
        status, code = HTTPStatus.NOT_FOUND, 'record_not_found'
    elif isinstance(exc, VersionConflict):
        status, code = HTTPStatus.CONFLICT, 'version_conflict'
    elif isinstance(exc, OSError):
        status, code = HTTPStatus.CONFLICT, 'source_unavailable'
    else:
        status, code = HTTPStatus.BAD_REQUEST, 'invalid_request'
    handler.write_json({'error': str(exc), 'code': code}, status=status)


def get_route(handler, parsed, paths):
    name = operation(parsed, GET_OPERATIONS)
    if not name:
        return False
    query = parse_qs(parsed.query)

    def value(key):
        return query.get(key, [''])[0]

    try:
        api = AssessmentWorkspace(paths)
        if name == 'catalog':
            result = api.catalog()
        elif name == 'slope':
            result = api.slope(value('slope_id'))
        elif name == 'case':
            result = api.case(value('case_id'))
        else:
            page = int(value('page')) if value('page') else None
            content, media = api.evidence(value('case_id'), value('context_id'), page)
            handler.send_response(HTTPStatus.OK)
            handler.send_header('Content-Type', media)
            handler.send_header('Content-Length', str(len(content)))
            handler.send_header('X-Content-Type-Options', 'nosniff')
            handler.end_headers()
            handler.wfile.write(content)
            return True
        handler.write_json(result)
    except (KeyError, OSError, ValueError, TypeError) as exc:
        handle_error(handler, exc)
    return True


def post_route(handler, parsed, paths):
    name = operation(parsed, POST_OPERATIONS)
    if not name:
        return False
    try:
        body = handler.read_json_body()
        if not isinstance(body, dict):
            raise ValueError('请求必须是 JSON 对象')
        api = AssessmentWorkspace(paths)
        if name == 'slopes':
            result = api.add_slope(body)
        elif name in {'link', 'unlink'}:
            result = api.link(body, unlink=name == 'unlink')
        elif name == 'create':
            result = api.create_case(body)
        elif name == 'evaluate':
            result = api.evaluate(body)
        elif name in {'supplement', 'correct'}:
            result = api.edit_evidence(body, correct=name == 'correct')
        else:
            result = api.review(body, select=name == 'select')
        handler.write_json(result)
    except (KeyError, OSError, ValueError, TypeError) as exc:
        handle_error(handler, exc)
    return True
