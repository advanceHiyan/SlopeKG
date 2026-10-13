"""Check the published local workflow; optional exports use unique names."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
from urllib.parse import quote, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from slopekg.storage import write_json
from slopekg.workflow_check import run_workflow_checks


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("核查请求不允许跳转")


class LocalClient:
    def __init__(self, base_url):
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError("仅允许本机SlopeKG的http服务地址，不带路径或凭据")
        self.base_url = base_url.rstrip("/")
        self.opener = build_opener(NoRedirect())

    def read(self, path, body=None):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("接口路径须为本机相对路径")
        request = Request(self.base_url + quote(path, safe="/%?=&:+"),
                          data=json.dumps(body).encode("utf-8") if body is not None else None,
                          headers={"Content-Type": "application/json"} if body is not None else {})
        with self.opener.open(request, timeout=30) as response:
            if urlsplit(response.url).netloc != urlsplit(self.base_url).netloc:
                raise ValueError("核查请求不能跳转到其他服务")
            return response.read()

    def get_json(self, path):
        return json.loads(self.read(path))

    def post_json(self, path, body):
        return json.loads(self.read(path, body))


@contextmanager
def isolated_server():
    # Do not call run_server(): that starts the live PDF directory watcher.
    from slopekg.server import SlopeKGHandler
    class QuietHandler(SlopeKGHandler):
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--base-url", default="http://127.0.0.1:8766")
    group.add_argument("--isolated", action="store_true", help="临时启动随机端口服务，不监听PDF目录")
    parser.add_argument("--exports", action="store_true", help="生成三份独立测试报告，不覆盖已有文件")
    parser.add_argument("--output", type=Path, help="保存功能核查JSON")
    args = parser.parse_args()
    def run(url):
        client = LocalClient(url)
        return run_workflow_checks(client.get_json, client.read, client.post_json if args.exports else None)
    if args.isolated:
        with isolated_server() as url:
            result = run(url)
    else:
        result = run(args.base_url)
    if args.output:
        write_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
