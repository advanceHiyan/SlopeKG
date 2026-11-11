"""Loopback-only DSH transport: project credentials stay outside the agent.

The gateway records complete provider requests/responses and enforces the
provider output ceiling on every generation, including SDK auxiliary calls.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
import urllib.error
import urllib.request
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .model_client import CompatibleVisionClient, ModelProfile, NoRedirect


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    # Windows may briefly deny replacement while a UI reader/antivirus owns a
    # handle. Keep the old complete JSON visible and retry the atomic replace;
    # a transient status-write collision must not terminate the running SDK.
    for attempt in range(12):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 11: raise
            time.sleep(0.05*(attempt+1))


class AgentGateway(AbstractContextManager):
    def __init__(self, profile: ModelProfile, root: Path, audit_dir: Path, *, max_calls=None, transport=None):
        self.profile = profile
        self.audit_dir = Path(audit_dir)
        self.max_calls = max_calls
        self.secret = secrets.token_urlsafe(32)
        self._key = None
        self._root = Path(root)
        self._lock = threading.Lock()
        self.receipts = []
        self.transport = transport or urllib.request.build_opener(NoRedirect()).open
        self.server = None

    def __enter__(self):
        self._key = CompatibleVisionClient(self.profile, self._root)._key()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *_args):
                pass

            def reject(self, status, message):
                body = json.dumps({'error': {'message': message, 'type': 'invalid_request_error'}}).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if self.headers.get('Authorization') != 'Bearer ' + owner.secret:
                    return self.reject(401, 'Invalid local gateway credential')
                # Images use inline data URLs. No Files API upload or deletion.
                if self.path.rstrip('/') not in {'/chat/completions', '/v1/chat/completions'}:
                    return self.reject(400, 'Only chat completions are enabled; use inline images')
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= 192 * 1024 * 1024:
                        return self.reject(413, 'Explicit request size limit exceeded')
                    payload = json.loads(self.rfile.read(size))
                    if payload.get('model') != owner.profile.model:
                        return self.reject(400, 'Model differs from the frozen project profile')
                    allowed = {'list_files','read_text','search_text','view_image','write_text','write_json','check_outputs'}
                    names = {t.get('function',{}).get('name') for t in payload.get('tools',[])}
                    if names-allowed:
                        return self.reject(400, 'SDK tool catalog changed; compatibility check required')
                    with owner._lock:
                        if owner.max_calls is not None and len(owner.receipts) >= owner.max_calls:
                            return self.reject(400, 'Run generation-call limit reached; task remains incomplete')
                        number = len(owner.receipts) + 1
                        receipt = {'number': number, 'started_at': utc_now(), 'status': 'started'}
                        owner.receipts.append(receipt)
                    owner.forward(self, payload, receipt)
                except (ValueError, TypeError, KeyError):
                    self.reject(400, 'Invalid JSON request')

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def forward(self, handler, payload, receipt):
        started = time.monotonic()
        number = receipt['number']
        request_file = self.audit_dir / f'{number:04d}.request.json'
        response_file = self.audit_dir / f'{number:04d}.response.sse'
        payload.pop('max_completion_tokens', None)
        payload.pop('max_output_tokens', None)
        payload['max_tokens'] = self.profile.max_output_tokens
        payload['stream'] = True
        payload['stream_options'] = {**payload.get('stream_options', {}), 'include_usage': True}
        # These are non-model diagnostics from DSH; do not upload local logs.
        payload.pop('dsh_session_log', None)
        payload.pop('dsh_plugin_packages', None)
        dump(request_file, payload)
        receipt.update(requested_max_output_tokens=payload['max_tokens'],
                       request_sha256=hashlib.sha256(request_file.read_bytes()).hexdigest(),
                       request_file=request_file.name, response_file=response_file.name,
                       usage=None, finish_reasons=[], stream_done=False)
        request = urllib.request.Request(self.profile.base_url.rstrip('/') + '/chat/completions',
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8'), method='POST',
            headers={'Authorization': 'Bearer ' + self._key, 'Content-Type': 'application/json'})
        sent_headers = False
        try:
            with self.transport(request, timeout=self.profile.timeout_seconds) as upstream:
                receipt['http_status'] = upstream.status
                handler.send_response(upstream.status)
                handler.send_header('Content-Type', 'text/event-stream; charset=utf-8')
                handler.send_header('Connection', 'close')
                handler.end_headers()
                handler.close_connection = True
                sent_headers = True
                with response_file.open('wb') as raw:
                    while True:
                        line = upstream.readline()
                        if not line:
                            break
                        raw.write(line)
                        raw.flush()
                        handler.wfile.write(line)
                        handler.wfile.flush()
                        if line.startswith(b'data:'):
                            data = line[5:].strip()
                            if data == b'[DONE]':
                                receipt['stream_done'] = True
                                continue
                            try:
                                event = json.loads(data)
                            except ValueError:
                                receipt['malformed_sse'] = True
                                continue
                            if event.get('usage'):
                                receipt['usage'] = event['usage']
                            if event.get('model'):
                                receipt['response_model'] = event['model']
                            for choice in event.get('choices', []):
                                if choice.get('finish_reason'):
                                    receipt['finish_reasons'].append(choice['finish_reason'])
                reasons = receipt['finish_reasons']
                receipt['status'] = ('complete' if receipt['stream_done'] and reasons
                    and all(v in {'stop', 'tool_calls'} for v in reasons)
                    and not receipt.get('malformed_sse') else 'incomplete')
                receipt['truncated'] = any(v in {'length', 'max_tokens'} for v in reasons)
        except urllib.error.HTTPError as exc:
            receipt.update(status='http_error', http_status=exc.code)
            response_file.write_bytes(exc.read())
            if not sent_headers:
                handler.reject(exc.code, 'Provider request failed; retained in the private run audit')
        except (OSError, ValueError) as exc:
            receipt.update(status='transport_error', error_type=type(exc).__name__)
            if not sent_headers:
                handler.reject(502, 'Provider transport failed; no automatic gateway retry')
        finally:
            receipt.update(finished_at=utc_now(), elapsed_seconds=round(time.monotonic()-started, 3))
            dump(self.audit_dir / f'{number:04d}.receipt.json', receipt)
            with self._lock:
                dump(self.audit_dir / 'receipts.json', self.receipts)

    def __exit__(self, *_args):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=5)
        self._key = None

    def usage(self):
        fields = ('prompt_tokens', 'completion_tokens', 'total_tokens',
                  'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens')
        return {**{k: sum((r.get('usage') or {}).get(k, 0) for r in self.receipts) for k in fields},
                'requests': len(self.receipts),
                'requests_without_usage': sum(r.get('usage') is None for r in self.receipts),
                'incomplete_requests': sum(r['status'] != 'complete' for r in self.receipts)}
