"""Launch the real risk workbench with two isolated, synthetic examples."""
from argparse import ArgumentParser
from dataclasses import replace
from hashlib import sha256
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import monotonic
from urllib.parse import parse_qs, urlencode, urlparse
import webbrowser

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from slopekg.config import PATHS
from slopekg.server import SlopeKGHandler
from slopekg.standard_assessment import workspace_http
from slopekg.standard_assessment.contract import enumeration, fact_record, number
from slopekg.standard_assessment.service import StandardAssessmentService
from slopekg.standard_assessment.workspace import AssessmentWorkspace


def seed(paths):
    workspace = AssessmentWorkspace(paths)
    origin = StandardAssessmentService(paths, database=paths.output_dir / 'assessment-debug/assessment.sqlite')
    operator = {'actor': '模拟演示', 'reason': '演示样例资料，由程序生成'}
    paths.raw_dir.mkdir()
    pdfs = {}
    first_slope = ''
    for case_id, label, condition in (
        ('demo-explicit', '演示 A · 资料齐全', '天然工况'),
        ('demo-missing', '演示 B · 评估条件待补', '评估条件未记载'),
    ):
        slope = workspace.add_slope({'label': label, **operator})['slope']['id']
        first_slope = first_slope or slope
        text = (f'模拟演示资料：{label}\n调查日期：2026-11-09。{condition}。\n'
                '按 GB/T 40112—2021 记录：中等发育。受威胁人数 8 人。')
        file = paths.raw_dir / f'{label}.pdf'
        with fitz.open() as pdf:
            pdf.new_page().insert_text((54, 100), text, fontname='china-s', fontsize=14)
            pdf.save(file)
        pdfs[case_id] = file
        relative, digest = file.relative_to(paths.root).as_posix(), sha256(file.read_bytes()).hexdigest()
        scope = {'subject': {'source_object_ref': case_id, 'object_kind': 'landslide_body'},
                 'time': {'kind': 'instant', 'start': '2026-11-09', 'end': '2026-11-09'},
                 'scenario': {'code': 'unspecified', 'raw': condition}}
        source_id, context_id = f'{case_id}-source', f'{case_id}-page'
        context = {'context_id': context_id, 'source_path': relative, 'source_sha256': digest,
                   'spans': [{'file_page_1_based': 1, 'quote': text}]}
        record = {'record_ref_id': source_id, 'source_path': relative, 'snapshot_sha256': digest}
        facts = [fact_record(variable, value, scope, source_refs=[source_id], evidence_refs=[context_id],
                             origin='manual_transcription', source_standard='GB/T 40112-2021', support='literal_matched')
                 for variable, value in [('development', enumeration('development', '中等发育')),
                                         ('threatened_people', number(8, 'person'))]]
        case = {'case_id': case_id, 'target_ref': label, 'scope': scope, 'hazard_type_supported': True,
                'source_hashes': {relative: digest}, 'resolution_version': 1,
                'fact_ids': [fact['fact_id'] for fact in facts], 'context_ids': [context_id], 'selections': {}}
        with origin.store.transaction() as db:
            origin.store.put('source_record', record, db)
            origin.store.put('evidence_context', context, db)
            for fact in facts:
                origin.store.put('fact_assertion', fact, db)
            origin.store.put('assessment_case', case, db)
        workspace.link({'slope_id': slope, 'workspace': 'debug', 'case_id': case_id,
                        'same_object_confirmed': True, **operator})
    return first_slope, pdfs


def serve(directory, port, open_browser):
    paths = replace(PATHS, raw_dir=directory / 'pdf', output_dir=directory,
                    web_data_dir=directory / 'data', assessment_test_only=True)
    first_slope, pdfs = seed(paths)
    page = '/web/slope-assessment.html?' + urlencode({'demo': 1, 'slope_id': first_slope})

    class DemoHandler(SlopeKGHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == '/':
                self.send_response(302)
                self.send_header('Location', page)
                self.end_headers()
            elif workspace_http.get_route(self, parsed, paths):
                return
            elif self.translate_path(self.path):
                SimpleHTTPRequestHandler.do_GET(self)
            else:
                self.send_error(404)

        def translate_path(self, url):
            parsed = urlparse(url)
            if parsed.path == '/web/assessment-debug.html':
                case_id = parse_qs(parsed.query).get('case', [''])[0]
                return str(pdfs[case_id]) if case_id in pdfs else ''
            if parsed.path in {'/web/slope-assessment.html', '/web/slope-assessment.js',
                               '/web/slope-assessment.css', '/web/pages.css',
                               '/web/navigation.js', '/web/navigation.css', '/web/api.js'}:
                return str(PATHS.web_dir / Path(parsed.path).name)
            return ''

        def do_POST(self):
            if not self.request_origin_allowed():
                self.send_error(403)
            elif not workspace_http.post_route(self, urlparse(self.path), paths):
                self.send_error(404)

    with ThreadingHTTPServer(('127.0.0.1', port), DemoHandler) as httpd:
        url = f'http://127.0.0.1:{httpd.server_port}/'
        print(url, flush=True)
        if open_browser:
            webbrowser.open(url)
        httpd.timeout = 1
        deadline = monotonic() + 3600
        while monotonic() < deadline:
            httpd.handle_request()


if __name__ == '__main__':
    cli = ArgumentParser(description=__doc__)
    cli.add_argument('--port', type=int, default=0)
    cli.add_argument('--no-browser', action='store_true')
    args = cli.parse_args()
    tmp = ROOT / '.tmp'
    tmp.mkdir(exist_ok=True)
    try:
        with TemporaryDirectory(prefix='risk-demo-', dir=tmp.resolve()) as directory:
            serve(Path(directory), args.port, not args.no_browser)
    except KeyboardInterrupt:
        pass
