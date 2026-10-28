"""Offline, escaped evidence pages generated from structured knowledge records."""
from html import escape
from pathlib import Path
from .storage import _atomic_write_text


def render_knowledge(run, target):
    esc = lambda v: escape(str(v or ''), quote=True)
    parts = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
        '<title>全文知识识别 · 对象与证据</title><style>body{font:16px/1.8 "Microsoft YaHei",sans-serif;margin:0;background:#f1f5f6;color:#23303d}main{max-width:1100px;margin:24px auto;background:white;padding:30px}table{border-collapse:collapse;width:100%;font-size:14px}td,th{border:1px solid #dce3e7;padding:9px;vertical-align:top;text-align:left}details{border:1px solid #dce3e7;padding:12px;margin:12px 0}summary{cursor:pointer;font-weight:bold}small{color:#607080}a{color:#176b91}blockquote{white-space:pre-wrap;margin:8px 0;padding:8px;background:#f4f7f9}.scroll{overflow:auto}@media(max-width:700px){main{margin:0;padding:16px}table{min-width:650px}}</style><main>',
        '<h1>全文知识识别：对象、事实与来源</h1>',
        '<p>此路线按全文对象收集非连续原文，再重组为事实和关系。模型核验仍待人工确认；图形页缺口单独列出。</p>',
        '<p>状态：' + esc(run['processing_status']) + ' · 模型调用 ' + str(run['summary']['calls']) +
        ' · 对象/概念 ' + str(run['summary']['entities']) + ' · 事实 ' + str(run['summary']['facts']) + '</p>']
    for doc in run['documents']:
        names = {e['id']: e['name'] for e in doc['entities']}
        parts.append('<h2>' + esc(Path(doc['path']).name) + '</h2>')
        coverage = doc['coverage']
        parts.append('<p>正文处理：' + esc(coverage['text_processing_status']) + '；有正文页 ' + str(coverage['native_text_pages']) +
            ' / 全部 ' + str(coverage['total_pages']) + '；归属分块 ' + str(coverage['attributed_chunks']) + '/' + str(coverage['total_chunks']) + '</p>')
        parts.append('<details><summary>图形内容尚未读取的物理页（' + str(len(coverage['unread_graphics_pages'])) +
            '）</summary><p>' + esc(', '.join(map(str, coverage['unread_graphics_pages']))) + '</p></details>')
        refs = doc.get('references', [])
        if refs:
            parts.append('<details><summary>目录线索与背景引用（' + str(len(refs)) + '项，未独立整理）</summary>')
            for ref in refs:
                parts.append('<p><strong>' + esc(ref['name']) + '</strong> · ' + esc(ref['salience']) +
                    ' · 原文页 ' + esc(', '.join(map(str, sorted({e['page'] for e in ref['mentions']})))) + '</p>')
            parts.append('</details>')
        for entity in doc['entities']:
            parts.append('<details id="' + esc(entity['id']) + '"><summary>' + esc(entity['name']) + ' · ' + esc(entity['kind']) +
                         ' · ' + str(len(entity['chunk_ids'])) + ' 个来源分块</summary><p>' + esc(entity['description']) + '</p>')
            parts.append('<p><small>别名：' + esc('；'.join(entity['aliases'])) + '；分块：' + esc(', '.join(entity['chunk_ids'])) + '</small></p>')
            facts = [f for f in doc['facts'] if f['entity_id'] == entity['id']]
            parts.append('<div class="scroll"><table><tr><th>事实</th><th>范围 / 性质 / 条件</th><th>核验状态与实际原文</th></tr>')
            for fact in facts:
                unit = fact['unit'] + ('/' + fact['unit_basis'] if fact['unit_basis'] else '')
                parts.append('<tr><td>' + esc(fact['field']) + '<br><strong>' + esc(fact['value'] + ' ' + unit) + '</strong></td><td>' +
                    esc('；'.join(fact[k] for k in ('scope', 'role', 'condition', 'time', 'qualifier') if fact[k])) + '</td><td>' +
                    esc(fact['status']) + '<br><small>' + esc(fact['reason']) + '</small>')
                for e in fact['evidence']:
                    href = Path(doc['path']).as_uri() + '#page=' + str(e['page'])
                    parts.append('<blockquote>' + esc(e['quote']) + '<br><a href="' + esc(href) + '">物理页 ' + str(e['page']) + '</a> · ' + esc(e['span_id']) + '</blockquote>')
                parts.append('</td></tr>')
            parts.append('</table></div>')
            relations = [r for r in doc['relations'] if r['entity_id'] == entity['id']]
            if relations:
                parts.append('<h3>有原文依据的关系候选</h3><ul>')
                for r in relations:
                    parts.append('<li>' + esc(r['predicate']) + ' → <a href="#' + esc(r['target_id']) + '">' +
                        esc(names.get(r['target_id'], r['target_id'])) + '</a> · ' + esc(r['status']) +
                        ''.join('<blockquote>' + esc(e['quote']) + '（物理页 ' + str(e['page']) + '）</blockquote>' for e in r['evidence']) + '</li>')
                parts.append('</ul>')
            parts.append('</details>')
    parts.append('<p><small>原文哈希、页码、分块归属、未处理批次和模型回执保存在同目录 run.json。未自动写入正式图谱。</small></p></main></html>')
    _atomic_write_text(Path(target), ''.join(parts))
