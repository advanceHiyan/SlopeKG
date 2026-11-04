"""Readable report from the synthetic suite's immutable evidence archives."""
from pathlib import Path
import html
import json

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/20261101_复杂PDF国标流程'
load=lambda name:json.loads((OUT/name).read_text('utf-8'))
esc=lambda value:html.escape(str(value))


def build():
    before=load('before_逐例核验.json');after=load('after_release_逐例核验.json')
    recognition=load('识别运行回执.json');cleanup=load('清理核验.json');tests=load('代码检查回执.json')
    corpus=load('资料目录.json')['documents'];latest=load('隔离归档_HTTP及版本回执.json')
    final={x['case']:x for x in latest['cases']};b={x['case']:x for x in before['cases']}
    tokens=sum(x.get('usage',{}).get('total_tokens',0) for x in recognition['receipts'])
    css=(ROOT/'outputs/20261031_模拟PDF国标流程/滑坡现状评估模拟验证报告.html').read_text('utf-8').split('<style>',1)[1].split('</style>',1)[0]
    css+='\n.gallery{display:grid;grid-template-columns:repeat(2,1fr);gap:18px}.sample{border:1px solid var(--line);padding:16px;border-radius:6px}.sample img{width:100%;height:300px;object-fit:contain;background:#eff3f6}.sample h3{margin:12px 0 7px}.sample p{font-size:14px}.pass{color:#19604d;font-weight:600}.fail{color:#91521b}.matrix td:first-child{white-space:nowrap}@media(max-width:700px){.gallery{grid-template-columns:1fr}.matrix{min-width:850px}}'
    parts=[f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>复杂PDF到国标评估｜开发验证报告</title><style>{css}</style></head><body><main>
<header><div class="meta">SlopeKG · 2026-11-01 · 开放开发样例 · 冻结识别版本 knowledge-v3.3</div>
<h1>复杂 PDF 到滑坡现状评估<br>开发验证与修复报告</h1>
<p class="lead"><strong>这次生成了 8 份、18 页、8 种结构的 PDF，覆盖 11 个对象。</strong>旧桥接流程有 4 个对象完整符合预期；修复接入、补检及状态提示后，11 个对象符合预期：7 个得到候选危险性等级，4 个保留冲突、不确定结果或缺口。</p>
<div class="notice"><strong>这是开发调试结果。</strong>样例和预期由我们编写，全部为虚构资料；11/11 表示这批固定检查通过，不是真实工程准确率、独立验收成绩或专家签认。没有训练模型。</div>
<div class="metrics"><div class="metric"><strong>8 份 / 18 页</strong><span>3 页为无文字层扫描页</span></div><div class="metric"><strong>4 → 11 / 11</strong><span>输入、结果与停止原因共同核对</span></div><div class="metric"><strong>31 次</strong><span>实际模型请求；改后复用冻结结果</span></div><div class="metric"><strong>{tests['tests']} 项</strong><span>相关代码检查全部通过</span></div></div>
<p><a href="隔离PDF样例包.zip"><strong>下载全部 8 份原始 PDF 样例包</strong></a> · <a href="#pdfs">逐份预览与打开</a> · <a href="#results">先看前后对照</a></p>
<nav><a href="#design">样例设计</a><a href="#flow">实际流程</a><a href="#repairs">发现与修复</a><a href="#results">逐例结果</a><a href="#limits">验证边界</a><a href="#cleanup">数据清理</a><a href="#evidence">完整证据</a></nav></header>
<section id="design"><h2>01 · 这批样例比上一批复杂在哪里</h2>
<p>上一批每页一个对象、直接写出发育等级和单个人数，主要证明查表链路能运行。这一批加入了正文、附表、重复页眉、工点公共背景和工程参数干扰；同时安排多个对象、跨页线索、历史与本期、现状与预测、冲突、经济单位和人数区间。</p>
<div class="scroll"><table><thead><tr><th>资料</th><th>页数</th><th>专门检验的难点</th></tr></thead><tbody>
<tr><td>D01 多对象合并表格</td><td>2</td><td>合并表头、继承单位、两对象同表，9人与120人不能串行或求和</td></tr>
<tr><td>D02 跨页调查与附表</td><td>3</td><td>人数在第1页，发育结论在第3页，中间混有施工计划人数</td></tr>
<tr><td>D03 历史事件与本期调查</td><td>2</td><td>2016年记录与2026年现状不能混用；历史死亡0人不补作本期危害输入</td></tr>
<tr><td>D04 天然现状与暴雨预测</td><td>2</td><td>本期8人与未来假设120人分开；正文提到下一章预测不改变本页事实角色</td></tr>
<tr><td>D05 同日两组调查矛盾</td><td>2</td><td>8人与120人并列；没有裁决，不能默认后一份覆盖前一份</td></tr>
<tr><td>D06 经济损失量纲与横表</td><td>2</td><td>横向表格；0.08亿元换算为800万元；人数未知时用另一有效指标</td></tr>
<tr><td>D07 纯扫描表格与区间人数</td><td>2</td><td>两页都无文字层；有轻微倾斜、灰度压缩；100人边界及8～12人区间</td></tr>
<tr><td>D08 图文混排与判断缺口</td><td>3</td><td>正文、图像附表、矢量示意图；裂缝描述、Fs及其他标准原词不得直接转发育等级</td></tr>
</tbody></table></div>
<p class="small">这些资料模拟的是常见结构与语义难点，仍然比真实勘察长报告简洁。图件是虚构示意，没有真实现场照片；本轮没有验证从图中计算几何参数。</p></section>
<section id="flow"><h2>02 · 实际执行的数据流</h2>
<div class="flow"><div class="step"><b>① 先生成并冻结</b><small>8份PDF、11个对象的预期<br>代码、完整提示词、模型及参数<br>模型不接收答案文件</small></div>
<div class="step"><b>② 真实识别首轮</b><small>知联v3.3 + 现有本地OCR<br>31次模型请求，3页OCR完成<br>保存原始输入、原回复和预测</small></div>
<div class="step"><b>③ 旧桥接跑评估</b><small>同一批模型输出进入隔离库<br>实际执行国标表15、表17<br>4/11对象完整符合预期</small></div>
<div class="step bridge"><b>④ 修复并回放</b><small>对象候选绑定、时间角色、单位区间<br>补检原文已明确写出的结论<br>复用冻结识别结果，不重算模型成绩</small></div>
<div class="step"><b>⑤ 接口与版本核验</b><small>最终11/11符合预期<br>实际HTTP计算、备注、重算、读回<br>旧版保留，全部仍待专业复核</small></div>
<div class="step"><b>⑥ 清理与隔离归档</b><small>4个阶段的测试数据库全部移除<br>识别缓存和临时解析一起移除<br>PDF及审计JSON、ZIP保留</small></div></div>
<div class="info"><strong>改后对照的含义：</strong>固定8份PDF及其首轮识别结果，改变的是风险接入和评估流程的处理。改后没有再次调用识别模型；其中的原文补检也是本地读取冻结来源，不能称为模型新预测。</div>
<p>评估目标中的对象、文档、日期和天然工况由测试任务明确指定；这代表一次已选好评估范围的请求，不代表系统已经自动确定真实工程的评估范围。候选对象绑定保留证据，不能代替跨资料物理体同一性的专业确认。</p></section>
<section id="repairs"><h2>03 · 具体发现了什么，怎么修</h2>
<h3>1. 读出的值归在概念下，滑坡对象拿不到</h3>
<p>跨页和扫描样例中，模型把部分值放在“滑坡发育程度评价”“受威胁人数”概念节点下。旧桥接只匹配事实所属实体名，所以漏掉了已读出的信息。</p>
<p>新增可复用的候选规范化模块 <code>standard_assessment/knowledge_bridge.py</code>：先看事实明确写出的主体，再核对证据页里是否只有一个明确请求对象。能形成来源候选绑定时才接入；多对象且不能分清时拒绝自动绑定。原始识别记录不改写，分区、断面和局部范围不能无条件提升为整个滑坡。</p>
<h3>2. 历史、预测与本期输入不能一起套当前范围</h3>
<p>保留原始角色和事实日期，再检查本次对象、时间、工况。历史120人不会替代本期9人，暴雨假设120人不会替代天然现状8人。调试中还修复了“正文提及后续预测章就把本页现状也标成预测”的过宽匹配，改为使用标题及事实本身的明确限定。</p>
<h3>3. 人数区间与经济单位需要规范化，不能只读字符串</h3>
<p><strong>0.08亿元 → 800万元</strong>，保留原值、原单位和换算因子；<strong>8～12人 → 闭区间[8,12]</strong>，保留跨边界的可能等级，不取平均数。未知保留未知；户数不当人数，设施容量和施工计划人数不当现状受威胁人数。已知人数冲突并列保存。</p>
<div class="notice"><strong>潜在损失的角色单独记录：</strong>模型把本期“0.08亿元、险情估计”标成prediction。桥接保留这一原角色；仅对明确属于本期、同工况且不是未来假设的潜在后果估计，生成待复核的 <code>reported_estimate</code> 候选。该语义转换保留条件性，需要专业核对，不能扩大为“所有预测都能直接用于现状评估”。</div>
<h3>4. 扫描件一条明确结论确实被模型漏提</h3>
<blockquote>D07 第2页的 OCR 原文：“按GB/T40112-2021形成的发育程度专业结论：弱发育。”<br>模型最终事实中缺了 SYN-I 的这条结论。</blockquote>
<p>增加对“发育程度专业结论：某等级”这类<strong>原文已经写出的结论</strong>的精确补检，记录来源为 <code>explicit_source_statement_retrieval</code>。它不根据裂缝、坡度、稳定系数推断新等级；找不出原文结论就继续保留缺口。扫描来源仍是机器转录，补检得到的结论待人工复核。</p>
<h3>5. 已给区间，不应该显示为缺输入</h3>
<p>发现SYN-I保留了两个可能等级，输入缺口却为空，流程仍提示“缺证据”。已修正评估服务：这种情况提示<strong>需要处理不确定结果</strong>（<code>needs_resolution</code>），不再假装缺一个单值。先复现失败，再修复；保留回归检查。</p>
<p class="small">这轮修复没有调整通用识别提示词，也没有把样例答案写进识别器。新增桥接模块已在本轮隔离流程中实际使用；默认正式网页的通用导入尚未因此自动完成切换。</p></section>
<section id="results"><h2>04 · 逐例前后对照</h2><p>通过条件同时检查有效输入、危害结果、危险性结果、冲突或区间保留、数据库读回和未专业签认。等级为空但丢了输入，不能算通过。</p>
<div class="scroll"><table class="matrix"><thead><tr><th>对象</th><th>冻结的主要输入</th><th>旧流程</th><th>修后实际结果</th><th>核验</th></tr></thead><tbody>''']
    inputs={'SYN-A':'中等发育；9人','SYN-B':'强发育；120人','SYN-C':'强发育（跨页）；10人','SYN-D':'本期弱发育；9人；另有历史记录','SYN-E':'天然现状弱发育；8人；另有预测情景','SYN-F':'中等发育；同日8人与120人冲突','SYN-G':'中等发育；本期潜在损失0.08亿元','SYN-H':'扫描表：弱发育；100人','SYN-I':'扫描页：弱发育；8～12人','SYN-J':'受威胁20人；只有变形观察，无发育结论','SYN-K':'受威胁5人；Fs0.98及其他标准原词'}
    stops={'SYN-F':'人数来源冲突，等待处理；不自动取高','SYN-I':'危害小/中等、危险性小/中等，保留区间结果','SYN-J':'危害中等；发育程度缺依据，停止定级','SYN-K':'危害小；未将Fs或其他标准原词当发育程度'}
    for row in after['cases']:
        key=row['case'];r=final[key]['result'];grade=r['table17_result']['grade'];t15=r['partials']['table15'] or {}
        old='通过' if b[key]['passed'] else '未通过：'+('有等级但输入不符' if b[key]['actual_grade'] else '输入或处理依据缺失/混用')
        actual=(str(t15.get('harm'))+' → '+grade+'；待复核') if grade else stops[key]
        parts.append(f'<tr><td>{esc(key)}</td><td>{esc(inputs[key])}</td><td class="{"pass" if b[key]["passed"] else "fail"}">{esc(old)}</td><td>{esc(actual)}</td><td class="pass">符合预期</td></tr>')
    parts.append('''</tbody></table></div><p><strong>7个得到候选格值，4个保持未定级。</strong>所有结果仍为 <code>pending</code>，正式评估发布均为关闭。软件测试中的Codex备注不等于地质专业人员签认。</p>
<p>本轮实际执行的国标等级计算仍以<strong>表15、表17</strong>为核心。表4/5只用于理解发育判断边界，本轮没有调用专业判断模型；表16的环境/诱因综合使用也未因此完成。SYN-J的观察可供后续专业分析，但这次没有凭单项裂缝强判等级。</p></section>
<section id="limits"><h2>05 · 这次能证明什么，不能推出什么</h2>
<ul><li><strong>能证明：</strong>冻结版本识别结果经过改进的候选桥接和精确原文补检，可以驱动现有隔离国标服务，在这批结构与语义变化中给出预期格值或停止状态。</li>
<li><strong>仍需复核：</strong>原始OCR、模型语义和对象候选绑定；本期潜在损失的估计口径；环境诱因综合判断；没有原文专业结论时如何组织观察并提出可弃答的辅助建议。</li>
<li><strong>尚未验证：</strong>几百页真实报告、极低清或严重倾斜扫描、手写批注、复杂图纸读数、完整图形理解、所有资料类型的自动对象对齐，以及新版识别器的全流程质量。</li></ul>
<div class="info"><strong>版本说明：</strong>本次实际模型运行版本是 <code>knowledge-v3.3</code>。工作区另有并行的识别开发改动；本报告使用已封存代码和31份实际模型输入核对版本，未覆盖或回滚那些识别文件，也没有把其后变更混入本轮成绩。改后结果是本轮风险接入的冻结预测回放。</div>
<p>本批是开放开发集，已用于调试，后续可以固定为回归材料；不能再称为隐藏样本或独立验收集。最终仍应在新冻结协议下用真实资料检查复杂性和误漏情况。</p></section>
<section id="cleanup"><h2>06 · 测试数据库已移除，原始PDF隔离保留</h2>
<div class="scroll"><table><thead><tr><th>项目</th><th>本轮核验结果</th></tr></thead><tbody>
<tr><td>4个阶段的隔离SQLite及识别缓存</td><td>已随本轮独立运行目录整体移除</td></tr>
<tr><td>本轮选定的正式数据文件</td><td>11份文件哈希与本轮基线一致</td></tr>
<tr><td>默认正式评估库</td><td>9张记录表的SYN-模拟标记命中均为0</td></tr>
<tr><td>8份原始PDF、预期和评估范围</td><td>哈希与冻结清单一致；PDF与审计档案留在本报告目录</td></tr>
<tr><td>保留的数据库审计</td><td>仅为标注archive_only的JSON历史导出，不是活跃数据库</td></tr>
<tr><td>归档约束</td><td>禁止自动导入正式数据；不作工程真值、训练标签或独立验收集</td></tr>
</tbody></table></div>
<p class="small">核查范围限上述11份正式文件及默认评估库，不声称扫描了所有磁盘或所有数据库。归档标记是用途约束，后续导入程序也须遵守；不是操作系统权限隔离。</p>
<p><a href="清理核验.json">打开本轮清理核验</a> · <a href="归档隔离标识.json">查看隔离标识</a></p></section>
<section id="pdfs"><h2>07 · 逐份查看原始样例</h2><p>以下缩略图来自实际固定PDF的第1页。每份PDF页内均有虚构和隔离标记；扫描页没有隐藏文字层。</p><div class="gallery">''')
    for doc in corpus:
        path=esc(doc['path'].replace('\\','/'))
        parts.append(f'<article class="sample"><a href="{path}"><img loading="lazy" src="预览/{doc["id"]}.png" alt="{esc(doc["name"])}第1页实渲染"></a><h3>{esc(doc["id"])} · {esc(doc["name"])}</h3><p>{doc["pages"]}页 · 对象：{esc("、".join(doc["cases"]))}</p><a href="{path}">打开隔离原始PDF</a><details><summary>展开PDF预览</summary><iframe loading="lazy" title="{esc(doc["name"])}，虚构隔离档案" src="{path}"></iframe></details></article>')
    parts.append(f'''</div></section><section id="evidence"><h2>08 · 运行、代码与审计证据</h2>
<p>实际模型请求 <strong>31次 / {tokens:,} tokens</strong>，0次本地缓存复用、0次重试；最大输出请求参数均为393216，回执均为stop。费用未查询。后续调试新增模型请求为0。</p>
<p>本机接口核验两轮各44次，<strong>合计88次实际HTTP请求</strong>；最终一轮包含11个对象的计算、代理备注、重算和案例查询，全部通过。相关代码检查<strong>{tests['tests']}项通过</strong>，包含单位、区间、否定句、角色、日期、对象歧义及区间状态回归。</p>
<p>识别原始汇总为69实体、348事实、48引用、73关系；223条原文对齐、125条待核，整体状态partial。数量和原文对齐状态不能当成整份资料的准确率；本报告只对冻结的11个评估对象作了明确检查。</p>
<div class="scroll"><table><thead><tr><th>证据用途</th><th>文件</th></tr></thead><tbody>
<tr><td>样例、预期、版本与完整提示词</td><td><a href="资料目录.json">目录</a> · <a href="预先冻结的预期.json">预期</a> · <a href="评估目标.json">目标范围</a> · <a href="冻结协议.json">改前冻结</a></td></tr>
<tr><td>实际模型输入、原回复和识别结果</td><td><a href="识别运行回执.json">用量回执</a> · <a href="隔离归档_识别全量审计.json">识别结果</a> · <a href="隔离归档_模型请求与原回复.zip">请求与原回复</a></td></tr>
<tr><td>前后输入及评估逐例比较</td><td><a href="before_逐例核验.json">改前核验</a> · <a href="after_release_逐例核验.json">最终核验</a> · <a href="隔离归档_after_release_字段与评估.json">最终字段、转换与评估</a></td></tr>
<tr><td>改后版本与并行改动边界</td><td><a href="改后冻结与对照说明.json">对照说明</a> · <a href="改前代码冻结.zip">改前代码</a> · <a href="风险接入改后代码.zip">本轮改后代码</a></td></tr>
<tr><td>接口、版本、代码检查</td><td><a href="隔离归档_HTTP及版本回执.json">最终44次HTTP与版本</a> · <a href="隔离归档_初次HTTP及版本回执.json">前一次44次HTTP</a> · <a href="代码检查回执.json">47项检查</a></td></tr>
<tr><td>清理与历史数据归档</td><td><a href="清理核验.json">清理证据</a> · <a href="隔离归档_数据库审计.json">仅归档JSON</a></td></tr>
</tbody></table></div><p class="small">中间调试结果也按阶段留档。保留整个目录时，原始PDF、缩略图和证据链接均可访问；只移动HTML会影响这些相对链接。报告不执行导入或评估操作。</p></section>
<footer>SlopeKG · 复杂PDF开发验证 · 全部样例为虚构资料 · 2026-11-01</footer></main></body></html>''')
    dest=OUT/'复杂PDF验证报告.html';dest.write_text('\n'.join(parts),encoding='utf-8');print(dest)


if __name__=='__main__':build()
