from urllib.parse import parse_qs

from . import debug_flow

PREFIX='/api/assessment-debug/'


def get_route(handler,parsed,paths):
    if not parsed.path.startswith(PREFIX):return False
    q=parse_qs(parsed.query);v=lambda k,default='':q.get(k,[default])[0]
    try:
        op=parsed.path.removeprefix(PREFIX)
        if op=='catalog':
            result=debug_flow.catalog(paths,{'route':v('route','knowledge'),'run_id':v('run_id','live'),'document':v('document')})
        elif op in {'flow','records','export'}:
            workspace=v('workspace','debug')
            if workspace not in {'main','debug'}:raise ValueError('无效评估工作区')
            if not (paths.output_dir/('assessment-debug' if workspace=='debug' else 'assessment-pilot')/'assessment.sqlite').is_file():raise KeyError('尚未创建该评估案例')
            if op=='export':
                content=debug_flow.export(paths,v('case_id'),workspace);handler.send_response(200)
                handler.send_header('Content-Type','application/zip');handler.send_header('Content-Length',str(len(content)))
                handler.send_header('Content-Disposition','attachment; filename="pdf-assessment-flow.zip"')
                handler.end_headers();handler.wfile.write(content);return True
            data=debug_flow.flow(paths,v('case_id'),v('result_id') or None,workspace)
            if op=='flow':result={k:val for k,val in data.items() if k!='records'}
            else:
                rows=data['records'].get(v('stage'))
                if rows is None:raise ValueError('无效评估步骤')
                offset=int(v('offset','0'));limit=int(v('limit','25'))
                if offset<0 or not 1<=limit<=100:raise ValueError('无效分页')
                result={'count':len(rows),'offset':offset,'limit':limit,'rows':rows[offset:offset+limit],
                        'case_id':data['case']['case_id'],'result_id':(data['selected_result'] or {}).get('result_id')}
        else:return False
        handler.write_json(result)
    except KeyError as exc:handler.write_json({'error':str(exc)},status=404)
    except (OSError,ValueError,TypeError) as exc:handler.write_json({'error':str(exc)},status=409)
    return True


def post_route(handler,parsed,paths):
    if parsed.path!=PREFIX+'prepare':return False
    try:
        body=handler.read_json_body()
        if not isinstance(body,dict):raise ValueError('请求必须为对象')
        handler.write_json(debug_flow.prepare(paths,body))
    except (OSError,ValueError,TypeError,KeyError) as exc:handler.write_json({'error':str(exc)},status=409)
    return True
