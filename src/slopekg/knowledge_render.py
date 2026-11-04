"""Offline, escaped evidence pages generated from structured knowledge records."""
from html import escape
from pathlib import Path
from .storage import _atomic_write_text


def render_knowledge(run, target):
    esc = lambda v: escape(str(v or ''), quote=True)
    parts = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
        '<title>知联 Knowledge · 对象与证据</title><style>body{font:16px/1.8 "Microsoft YaHei",sans-serif;margin:0;background:#f1f5f6;color:#23303d}main{max-width:1100px;margin:24px auto;background:white;padding:30px}table{border-collapse:collapse;width:100%;font-size:14px}td,th{border:1px solid #dce3e7;padding:9px;vertical-align:top;text-align:left}details{border:1px solid #dce3e7;padding:12px;margin:12px 0}summary{cursor:pointer;font-weight:bold}small{color:#607080}a{color:#176b91}blockquote{white-space:pre-wrap;margin:8px 0;padding:8px;background:#f4f7f9}.scroll{overflow:auto}@media(max-width:700px){main{margin:0;padding:16px}table{min-width:650px}}</style><main>',
        '<h1>知联 Knowledge：对象、事实与来源</h1>',
        '<p>此路线复用基读表格及已有OCR，按全文对象聚齐非连续证据并合批整理。原文对齐是本地检查，独立模型核验仍待人工确认；图形页缺口单独列出。</p>',
        '<p>状态：' + esc(run['processing_status']) + ' · 模型调用 ' + str(run['summary']['calls']) +
        ' · 对象/概念 ' + str(run['summary']['entities']) + ' · 事实 ' + str(run['summary']['facts']) + '</p>']
    for doc in run['documents']:
        names = {e['id']: e['name'] for e in doc['entities']}
        parts.append('<h2>' + esc(Path(doc['path']).name) + '</h2>')
        coverage = doc['coverage']
        if coverage.get('scope') == 'selected_pages':
            parts.append('<p><strong>本次仅测试物理页 ' + esc(', '.join(map(str, coverage['selected_pages']))) +
                         '；其余页未测试，整份文件保持部分完成。</strong></p>')
        inventory, plan = doc.get('source_inventory', {}), doc.get('call_plan', {})
        parts.append('<p>复用表格 ' + str(inventory.get('tables', 0)) + '；OCR文字 ' +
            str(inventory.get('ocr_rows_reused', 0)) + '条；整理批次 ' + esc(plan.get('synthesis_batches') or '尚未安排') +
            '；可选复核上限 ' + str(plan.get('review_limit', 0)) + '次。</p>')
        parts.append('<p>正文处理：' + esc(coverage['text_processing_status']) + '；有正文页 ' + str(coverage['native_text_pages']) +
            ' / 全部 ' + str(coverage['total_pages']) + '；归属分块 ' + str(coverage['attributed_chunks']) + '/' + str(coverage['total_chunks']) + '</p>')
        parts.append('<details><summary>图形内容尚未读取的物理页（' + str(len(coverage['unread_graphics_pages'])) +
            '）</summary><p>' + esc(', '.join(map(str, coverage['unread_graphics_pages']))) + '</p></details>')
        accounting=doc.get('source_accounting',{})
        residuals=accounting.get('unrepresented_numeric_sources',[])
        if residuals:
            parts.append('<details><summary>尚未重组或分类的数值来源（'+str(len(residuals))+
                '个单元）</summary><p>保留的原文数值可能是属性、编号或背景引用，不能直接当事实或漏项计分。</p>')
            for row in residuals:
                parts.append('<p>物理页 '+str(row['page'])+' · '+esc(row['span_id'])+'：'+esc(', '.join(row['tokens']))+'</p>')
            parts.append('</details>')
        if accounting.get('unassigned_units'):
            parts.append('<details><summary>尚未归入对象的来源单元（'+str(accounting['unassigned_units'])+
                '）</summary><p>这些原文仍被保留；归属未明不能猜配对象，也不表示整页已经整理完成。</p>')
            for page in accounting['pages']:
                if page['unassigned_unit_ids']:
                    parts.append('<p>物理页 '+str(page['page'])+'：'+esc(', '.join(page['unassigned_unit_ids']))+'</p>')
            parts.append('</details>')
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
            dossier = next((p for p in doc.get('object_pages', []) if p['entity_id'] == entity['id']), {})
            links = [*dossier.get('child_ids', []), *dossier.get('related_topic_ids', [])]
            if links:
                parts.append('<p>子对象与相关主题：' + ' · '.join('<a href="#' + esc(key) + '">' +
                    esc(names.get(key, key)) + '</a>' for key in links) + '</p>')
            facts = [f for f in doc['facts'] if f['entity_id'] == entity['id']]
            parts.append('<div class="scroll"><table><tr><th>事实</th><th>范围 / 性质 / 条件</th><th>核验状态与实际原文</th></tr>')
            for fact in facts:
                unit = fact['unit'] + ('/' + fact['unit_basis'] if fact['unit_basis'] else '')
                parts.append('<tr><td>' + esc(fact['field']) + '<br><strong>' + esc(fact['value'] + ' ' + unit) + '</strong></td><td>' +
                    esc('；'.join(fact[k] for k in ('scope', 'role', 'condition', 'time', 'qualifier') if fact[k])) + '</td><td>' +
                    esc({'source_aligned':'原文对齐 · 未独立复核','verified_model':'模型复核通过 · 待人工确认',
                         'pending':'待审','rejected':'已拒绝'}.get(fact['status'], fact['status'])) +
                    '<br><small>' + esc(fact['reason']) + '</small>')
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
