"""Incremental projection of saved DSH notifications, never a synthetic transcript."""
from collections import OrderedDict
from pathlib import Path
import json
import threading
import copy

_lock=threading.RLock()
_cache=OrderedDict()

def _text(content):
    return '\n'.join(b.get('text','') for b in content if isinstance(b,dict) and b.get('type')=='text')

def trace_snapshot(root, *, offset=0, limit=40, event=None):
    path=Path(root)/'control/events.jsonl'
    with _lock:
        stat=path.stat() if path.exists() else None
        key=str(path)
        cache=_cache.get(key)
        if cache is None or (stat and (stat.st_size<cache['position'] or stat.st_ino!=cache['inode'])):
            cache={'position':0,'inode':stat.st_ino if stat else None,'rows':OrderedDict(),'raw':{},'calls':{},'active':None,'malformed':0}
            _cache[key]=cache
        _cache.move_to_end(key)
        while len(_cache)>4:_cache.popitem(last=False)
        if stat:
            with path.open('rb') as stream:
                stream.seek(cache['position'])
                while True:
                    line=stream.readline()
                    if not line or not line.endswith(b'\n'):break
                    cache['position']=stream.tell()
                    try:n=json.loads(line)
                    except ValueError:cache['malformed']+=1;continue
                    e=n.get('params',{}).get('event',{})
                    kind=e.get('type');data=e.get('data',{});seq=e.get('seq')
                    if kind=='step/start':cache['active']={'step':data.get('step'),'time':e.get('time')}
                    elif kind=='step/end':cache['active']=None
                    elif kind=='assistant/chunk':
                        chunk=data.get('chunk',{})
                        if chunk.get('type')=='text-delta':
                            rowkey='assistant-'+str(data.get('step'))
                            row=cache['rows'].setdefault(rowkey,{'id':rowkey,'kind':'assistant','seq':seq,'time':e.get('time'),'step':data.get('step'),'text':'','streaming':True})
                            row['text']+=chunk.get('text','')
                    elif kind in {'user/message','assistant/message'}:
                        content=data.get('message',data).get('content',[])
                        text=_text(content)
                        if text:
                            rowkey=('assistant-'+str(data.get('step'))) if kind=='assistant/message' else 'user-'+str(seq)
                            cache['rows'][rowkey]={'id':rowkey,'kind':kind.split('/')[0],'seq':seq,'time':e.get('time'),'step':data.get('step'),'text':text,'streaming':False}
                            cache['raw'][str(seq)]=e
                    elif kind=='tool/call':
                        call=data.get('callId');args=data.get('arguments',{})
                        try:args=json.loads(args) if isinstance(args,str) else args
                        except ValueError:args={}
                        row={'id':'tool-'+str(seq),'kind':'tool','seq':seq,'time':e.get('time'),'step':data.get('step'),
                             'tool':data.get('name'),'path':args.get('path','') if isinstance(args,dict) else '',
                             'status':'running','detail_seq':seq}
                        cache['rows'][row['id']]=row;cache['calls'][call]=row['id'];cache['raw'][str(seq)]={'call':e}
                    elif kind=='tool/result':
                        msg=data.get('message',{})
                        call=msg.get('source',{}).get('callId');rowkey=cache['calls'].get(call)
                        if rowkey:
                            row=cache['rows'][rowkey]
                            blocks=msg.get('content',[])
                            row['status']='error' if any(b.get('isError') for b in blocks if isinstance(b,dict)) else 'complete'
                            row['finished_at']=e.get('time')
                            cache['raw'][str(row['seq'])]['result']=e
        if event is not None:
            value=cache['raw'].get(str(event))
            if value is None:raise ValueError('事件不存在或尚未完成')
            return {'event':copy.deepcopy(value)}
        rows=list(cache['rows'].values())
        start=max(0,len(rows)-limit) if offset<0 else offset
        return {'rows':copy.deepcopy(rows[start:start+limit]),'count':len(rows),'offset':start,'active':copy.deepcopy(cache['active']),
                'malformed_records':cache['malformed'],'source':'control/events.jsonl','read_bytes':cache['position']}
