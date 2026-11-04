"""HTTP surface for open checkpoints and persistent debug records."""
from http import HTTPStatus
from urllib.parse import parse_qs

from .debug_datasets import list_datasets, get_dataset, dataset_hash, save_dataset
from .debug_history import (list_records, record_detail, append_review, save_snapshot,
                            export_record, compare_records)
from .debug_tests import start_test


PREFIX = '/api/recognition-debug/'


def get_workspace_route(handler, parsed, paths):
    endpoint = parsed.path.removeprefix(PREFIX)
    if endpoint not in {'datasets', 'dataset', 'history', 'history/record', 'history/export', 'history/compare'}:
        return False
    query = parse_qs(parsed.query)
    value = lambda name, default='': query.get(name, [default])[0]
    try:
        if endpoint == 'datasets':
            data = list_datasets(paths)
        elif endpoint == 'dataset':
            dataset = get_dataset(paths, value('id'))
            data = {**dataset, 'dataset_hash': dataset_hash(dataset)}
        elif endpoint == 'history':
            data = list_records(paths, q=value('q'), route=value('route'), offset=int(value('offset', '0')), limit=int(value('limit', '30')))
        elif endpoint == 'history/record':
            data = record_detail(paths, value('id'))
        elif endpoint == 'history/compare':
            data = compare_records(paths, value('left'), value('right'))
        else:
            record_id = value('id')
            content = export_record(paths, record_id)
            handler.send_response(HTTPStatus.OK)
            handler.send_header('Content-Type', 'application/zip')
            handler.send_header('Content-Disposition', f'attachment; filename="debug-{record_id}.zip"')
            handler.send_header('Content-Length', str(len(content)))
            handler.end_headers()
            handler.wfile.write(content)
            return True
        handler.write_json(data)
    except (ValueError, OSError, TypeError, KeyError) as exc:
        handler.write_json({'error': str(exc)}, status=HTTPStatus.BAD_REQUEST)
    return True


def post_workspace_route(handler, parsed, paths):
    if not parsed.path.startswith(PREFIX):
        return False
    endpoint = parsed.path.removeprefix(PREFIX)
    try:
        body = handler.read_json_body()
        if not isinstance(body, dict):
            raise ValueError('请求必须是JSON对象')
        status = HTTPStatus.OK
        if endpoint == 'datasets':
            data, status = save_dataset(paths, body), HTTPStatus.CREATED
        elif endpoint == 'tests':
            data, status = start_test(paths, body), HTTPStatus.ACCEPTED
        elif endpoint == 'history':
            data, status = save_snapshot(paths, route=body.get('route'), run_id=body.get('run_id'),
                document=body.get('document'), label=body.get('label')), HTTPStatus.CREATED
        elif endpoint == 'history/review':
            data = append_review(paths, body.get('id'), point_id=body.get('point_id', ''),
                verdict=body.get('verdict', 'note'), note=body.get('note', ''), label=body.get('label'), tags=body.get('tags'))
        else:
            handler.write_json({'error': 'not found'}, status=HTTPStatus.NOT_FOUND)
            return True
        handler.write_json(data, status=status)
    except (ValueError, OSError, TypeError, KeyError) as exc:
        handler.write_json({'error': str(exc)}, status=HTTPStatus.BAD_REQUEST)
    return True
