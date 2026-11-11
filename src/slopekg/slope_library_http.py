"""HTTP adapter for source-backed slope dossiers."""
from urllib.parse import parse_qs
from . import slope_library as library
from . import slope_identity as identity


def route(handler, parsed, paths):
    if not parsed.path.startswith('/api/slope-library'):
        return False
    try:
        query = parse_qs(parsed.query)
        get = lambda key: query.get(key, [''])[0]
        if parsed.path == '/api/slope-library/identity':
            if handler.command == 'GET':
                result = identity.preview(paths, binding_id=get('binding_id'), slope_id=get('slope_id'),
                                          namespace=get('namespace'), external_id=get('external_id'))
            else:
                body = handler.read_json_body()
                if not isinstance(body, dict):
                    raise ValueError('请提交边坡身份信息')
                result = identity.update(paths, body)
            handler.write_json(result)
            return True
        if handler.command == 'GET' and parsed.path in {'/api/slope-library/page', '/api/slope-library/source', '/api/slope-library/figure'}:
            if parsed.path.endswith('/figure'):
                content = library.figure_image(paths, get('snapshot'), int(get('index')))
                media = 'image/png'
            elif parsed.path.endswith('/page'):
                content = library.page_image(paths, get('source'), int(get('page')))
                media = 'image/png'
            else:
                content = library.source_file(paths, get('source')).read_bytes()
                media = 'application/pdf'
            handler.send_response(200)
            handler.send_header('Content-Type', media)
            handler.send_header('X-Content-Type-Options', 'nosniff')
            handler.send_header('Content-Length', str(len(content)))
            handler.end_headers()
            handler.wfile.write(content)
            return True
        if parsed.path != '/api/slope-library':
            raise KeyError('没有这个资料库接口')
        if handler.command == 'GET':
            result = (library.detail(paths, slope_id=get('slope_id'), binding_id=get('binding_id'))
                      if get('slope_id') or get('binding_id') else
                      library.catalog(paths, snapshot=get('snapshot'), run_id=get('run'), route=get('route')))
        else:
            body = handler.read_json_body()
            if not isinstance(body, dict) or body.get('operation') not in {'ingest', 'bind'}:
                raise ValueError('请选择保存识别来源或核对归属')
            result = library.ingest_saved(paths, body) if body['operation'] == 'ingest' else library.bind(paths, body)
        handler.write_json(result)
    except identity.IdentityConflict as exc:
        handler.write_json({'error': str(exc), 'error_type': 'identity_conflict'}, status=409)
    except KeyError as exc:
        handler.write_json({'error': str(exc)}, status=404)
    except (ValueError, TypeError, OSError) as exc:
        handler.write_json({'error': str(exc)}, status=400)
    return True
