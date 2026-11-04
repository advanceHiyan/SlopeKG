"""Read-only check of the exact workspace, loaded code and debug API capability."""
import argparse
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from slopekg.provenance import source_revision


def probe(port, *, fetch=None):
    base = f'http://127.0.0.1:{port}'
    expected = source_revision(ROOT / 'src/slopekg')
    if fetch is None:
        def fetch(endpoint):
            with urllib.request.urlopen(base + endpoint, timeout=3) as response:
                return json.load(response)
    result = {'state': 'stopped', 'port': port, 'root': str(ROOT), 'expected_revision': expected, 'pid': None}
    try:
        health = fetch('/api/health')
    except (urllib.error.URLError, OSError, ValueError):
        health = None
    if health is not None:
        if (not isinstance(health, dict) or health.get('app') != 'SlopeKG' or not isinstance(health.get('root'), str)
                or str(Path(health['root']).resolve()).casefold() != str(ROOT.resolve()).casefold()):
            return {**result, 'state': 'unknown', 'reason': '该端口属于其他服务或其他项目'}
        result.update(pid=health.get('runtime', {}).get('pid'), loaded_revision=health.get('runtime', {}).get('code_revision'))
        if health.get('status') == 'ready' and result['loaded_revision'] == expected:
            required = [('/api/recognition-debug/catalog', 'stages'),
                        ('/api/recognition-debug/datasets', 'datasets'), ('/api/recognition-debug/history', 'records')]
            try:
                if all(isinstance(fetch(endpoint).get(key), list) for endpoint, key in required):
                    return {**result, 'state': 'ready'}
            except (urllib.error.URLError, OSError, ValueError, AttributeError):
                pass
            return {**result, 'state': 'degraded', 'reason': '服务版本匹配，但调试接口不可用，请检查服务日志'}
    else:
        try:
            status = fetch('/api/status')
        except (urllib.error.URLError, OSError, ValueError):
            return result
        if not isinstance(status, dict) or 'graph_available' not in status or not isinstance(status.get('runtime'), dict):
            return {**result, 'state': 'unknown', 'reason': '该端口不是可识别的SlopeKG服务'}
        result.update(pid=status['runtime'].get('pid'), loaded_revision=status['runtime'].get('code_revision'))
    # A stale service is only replaceable when its work is idle. Missing newer
    # APIs on old versions are allowed; unavailable job state is not.
    try:
        lock = ROOT / 'output/demo/.pipeline.lock'
        if lock.is_file() and json.loads(lock.read_text(encoding='utf-8')).get('pid') == result['pid']:
            return {**result, 'state': 'busy', 'reason': '该服务仍持有识别任务锁，不能中断'}
        for endpoint in ['/api/pipeline/jobs/latest', '/api/rules/jobs/latest']:
            try:
                job = fetch(endpoint)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    continue
                raise
            if job.get('status') in {'queued', 'running'}:
                return {**result, 'state': 'busy', 'reason': '已有任务运行，等待完成后才能重载旧服务'}
        try:
            history = fetch('/api/recognition-debug/history?limit=100')
            if any(r.get('owner_pid') == result['pid'] and r.get('status') in {'queued', 'running'} for r in history.get('records', [])):
                return {**result, 'state': 'busy', 'reason': '数据集测试正在运行，不能中断'}
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
    except (urllib.error.URLError, OSError, ValueError, AttributeError):
        return {**result, 'state': 'unknown', 'reason': '无法确认旧服务是否空闲，未自动停止'}
    return {**result, 'state': 'stale', 'reason': '后端未加载当前代码或缺少调试接口'}


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port must be 1..65535')
    print(json.dumps(probe(args.port), ensure_ascii=False))
