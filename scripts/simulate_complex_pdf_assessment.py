"""Open synthetic PDF development suite. Never imports into the active database.

The inputs/expected answers are authored before recognition. Expected answers
are only read by score(), never by recognize() or assess(). This is a debugging
suite, not a hidden evaluation set or geological validation.
"""
from __future__ import annotations

import argparse
import copy
from dataclasses import asdict, replace
import hashlib
import html
import io
import json
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
OUT = ROOT / 'outputs/20261101_复杂PDF国标流程'
WORK = ROOT / '.private/reviews/20261101_complex_pdf_runtime'
DATE = '2026-10-20'


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def catalog():
    """Source design, deliberately separate from model inputs."""
    return [
        {'id': 'D01', 'name': '多对象合并表格', 'pages': 2, 'cases': ['SYN-A', 'SYN-B']},
        {'id': 'D02', 'name': '跨页调查与附表', 'pages': 3, 'cases': ['SYN-C']},
        {'id': 'D03', 'name': '历史事件与本期调查', 'pages': 2, 'cases': ['SYN-D']},
        {'id': 'D04', 'name': '天然现状与暴雨预测', 'pages': 2, 'cases': ['SYN-E']},
        {'id': 'D05', 'name': '同日两组调查矛盾', 'pages': 2, 'cases': ['SYN-F']},
        {'id': 'D06', 'name': '经济损失量纲与横表', 'pages': 2, 'cases': ['SYN-G']},
        {'id': 'D07', 'name': '纯扫描表格与区间人数', 'pages': 2, 'cases': ['SYN-H', 'SYN-I']},
        {'id': 'D08', 'name': '图文混排与判断缺口', 'pages': 3, 'cases': ['SYN-J', 'SYN-K']},
    ]


def prepare():
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, Table, TableStyle

    if (OUT / '冻结协议.json').exists():
        raise RuntimeError('Frozen suite already exists; do not regenerate it in place')
    native = WORK / 'native'; native.mkdir(parents=True, exist_ok=True)
    pdfdir = OUT / '原始PDF'; pdfdir.mkdir(parents=True, exist_ok=True)
    pdfmetrics.registerFont(TTFont('SimSunFixture', 'C:/Windows/Fonts/simsun.ttc', subfontIndex=0))
    font = 'SimSunFixture'
    style = ParagraphStyle('p', fontName=font, fontSize=11, leading=19, textColor=colors.HexColor('#243b4c'))
    small = ParagraphStyle('t', parent=style, fontSize=10, leading=16)

    def page(c, title, number, subtitle='', landscape=False):
        width, height = (842, 595) if landscape else (595, 842)
        c.setPageSize((width, height))
        c.setFillColor(colors.HexColor('#eef3f7')); c.rect(0, height - 68, width, 68, fill=1, stroke=0)
        c.setFont(font, 10); c.setFillColor(colors.HexColor('#8b5200'))
        c.drawString(32, height - 24, '虚构软件调试档案 / SIMULATED / 禁止作为工程资料导入')
        c.setFont(font, 16); c.setFillColor(colors.HexColor('#173f59')); c.drawString(32, height - 49, title)
        c.setFont(font, 9); c.setFillColor(colors.grey)
        c.drawString(32, 23, f'隔离开发样例 | {number} | 无真实工程或个人信息')
        c.drawRightString(width-32, 23, 'SlopeKG / 原始测试PDF')
        y = height - 92
        if subtitle:
            p = Paragraph(html.escape(subtitle), small); _, h = p.wrap(width-64, 80); p.drawOn(c, 32, y-h); y -= h+14
        c._fixture_width=width;c._fixture_y=y
        return width, y

    def text(c, y, content, width=531, x=32):
        for part in content.split('\n'):
            p=Paragraph(html.escape(part),style);_,h=p.wrap(width,1000)
            if y-h<50:raise ValueError('Fixture page overflows: '+part[:30])
            p.drawOn(c,x,y-h);y-=h+12
        c._fixture_y=y
        return y

    def table(c, y, rows, widths, spans=()):
        cells=[[Paragraph(html.escape(str(v)),small) for v in row] for row in rows]
        t=Table(cells,colWidths=widths)
        commands=[('BACKGROUND',(0,0),(-1,0),colors.HexColor('#dceaf2')),
                  ('GRID',(0,0),(-1,-1),0.5,colors.HexColor('#9fb3bf')),
                  ('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),9),
                  ('BOTTOMPADDING',(0,0),(-1,-1),9)]
        commands += [('SPAN',a,b) for a,b in spans]
        t.setStyle(TableStyle(commands));_,h=t.wrap(sum(widths),1000)
        if y-h<50:raise ValueError('Fixture table overflows')
        t.drawOn(c,32,y-h);c._fixture_y=y-h-18
        return y-h-18

    class FixtureCanvas(canvas.Canvas):
        def showPage(self):
            # Real reports contain nearby engineering facts and long prose.
            # Fill available space with explicitly common background, never
            # with a second hidden copy of the expected risk answer.
            y=getattr(self,'_fixture_y',0);width=getattr(self,'_fixture_width',595)-64
            if y>285:
                y=text(self,y,'勘察附记：下列为工点公共背景，不作为任何滑坡发育程度或危害程度的直接结论。',width=width)
                y=table(self,y,[['勘察资料项','原始记录摘要','使用范围'],['地层与岩性','表层碎石土；下伏风化砂岩','工点共同背景'],['钻探记录','ZK01孔深12.5m，填土厚约2.4m','仅钻孔局部'],['水文观测','局部孔内水位埋深2.0m','不代表整个坡体']], [width*0.23,width*0.48,width*0.29])
            notes=[
                '资料整理说明：本档案同时保留现场观察、工程参数、原报告术语和建议。不同来源的数字具有不同含义，应保留单位、测量位置及原始上下文，不能因为相邻就组成同一判断。',
                '地质环境补述：斜坡中下部可见碎石土覆盖，局部基岩露头，沟谷有季节性地表水。钻孔数据只代表钻孔附近条件，不能未经说明外推到整个物理坡体。',
                '调查工作记录：地形测绘、踏勘与资料核对分别记录。现场观察日期与报告编写日期分开保留；计算工况不等于现场发生状态，历史事件日期也不等于本期调查日期。',
                '工程布置资料说明：拟建排水沟、挡护结构和施工组织属于工程设计或建议。设施容量、计划施工人员和历史车流量不能替代现状受威胁人数，未统计项目保持未知。',
                '证据与复核说明：附表与正文保留原有不同表述；没有给出来源优先级或替代关系的记录并列保存。对对象、时期或量义存在疑问时，应指出具体证据位置，等待核对。',
                '图件说明：示意位置、钻孔编号及局部剖面用于表达资料关系，并非统一比例尺的实测成果。断面上的计算稳定系数不能无条件当成整个滑坡体的发育等级。',
            ]
            for note in notes:
                para=Paragraph(html.escape(note),small);_,h=para.wrap(width,1000)
                if y-h<62:break
                para.drawOn(self,32,y-h);y-=h+12
            self._fixture_y=0
            super().showPage()

    shared=f'调查日：{DATE}。采用 GB/T 40112-2021；评价整个滑坡体，天然工况；险情口径。'
    documents={}
    for doc in catalog():
        path=native/(doc['id']+'.pdf');c=FixtureCanvas(str(path),pagesize=(595,842))
        d=doc['id']
        if d=='D01':
            _,y=page(c,'河谷公路两处滑坡调查汇总',1,shared)
            y=text(c,y,'本工点包含两个独立滑坡，分别建立记录，不合并威胁人数。坡号与附图保持一致。\n表 1 现状分项调查（同一时期，不同对象）')
            y=table(c,y,[['对象','发育程度（GB/T 40112）','受威胁',''],['','','人数（人）','潜在损失'],['SYN-A滑坡','中等发育','9','未统计'],['SYN-B滑坡','强发育','120','未知']], [115,160,100,156], [((0,0),(0,1)),((1,0),(1,1)),((2,0),(3,0))])
            text(c,y,'注：人数是现状调查值；未统计经济损失不代表经济损失为零。两处对象的专业发育结论只适用于各自整个滑坡。')
            c.showPage();_,y=page(c,'两处滑坡调查补充及工程布置',2,shared)
            text(c,y,'SYN-A滑坡位于河谷左岸，SYN-B滑坡位于右岸；二者没有共同滑面。\n地质环境：土质滑坡，局部基岩出露。诱发因素调查记录：降雨。\n拟建设施可容纳200人，这是未来设施容量，不是本期受威胁人数。\n现状调查仅使用前页表1，各对象人数不得取整张表的最大值或总和。')
        elif d=='D02':
            _,y=page(c,'SYN-C滑坡现场调查记录',1,shared)
            text(c,y,'对象编号：SYN-C；本报告各附表均指该整个滑坡体。\n受威胁人数为 10 人，详见后附清点记录；潜在经济损失尚未统计。\n滑体长约210m、宽约80m；坡面局部有植被覆盖。\n本页记录形态与受威胁情况，发育程度专业结论见第3页附表2。')
            c.showPage();_,y=page(c,'SYN-C滑坡勘察附记',2,shared)
            text(c,y,'SYN-C滑坡现场观察续页：前缘局部临空，有季节性地表水流经。\n道路改线施工拟投入工人60名，此数是施工计划，不是本期受威胁人口。\n既有道路设计荷载与车流统计只作工程背景。附表2是本期发育判断的来源。\n本页没有新增受威胁人数或发育程度结论。')
            c.showPage();_,y=page(c,'SYN-C滑坡专业判断附表',3,shared)
            y=table(c,y,[['附表2 项目','结论及适用范围'],['对象','SYN-C滑坡，整个滑坡体'],['依据标准','GB/T 40112-2021'],['本期发育程度','强发育'],['评价时点及工况',f'{DATE}；天然工况']], [170,361])
            text(c,y,'本表结论须与第1页的同对象受威胁人数配套使用；本档案为虚构软件试验资料，无实际专业人员签名。')
        elif d=='D03':
            _,y=page(c,'SYN-D滑坡历史灾害回顾',1,'历史章节，不代表本期调查状态。')
            text(c,y,'对象：SYN-D滑坡，整个滑坡体。\n2016-07-10 曾发生滑动，历史记录记载发育程度为强发育，受威胁人数120人。记录死亡0人，属于当年事件信息。\n历史记录采用 GB/T 40112-2021 的分级术语作回顾整理；不能据此把历史状态填入2026年现状。\n本期评价时点为2026-10-20，现状调查见下一页。')
            c.showPage();_,y=page(c,'SYN-D滑坡本期现状调查',2,shared)
            text(c,y,'SYN-D滑坡本期发育程度专业结论：弱发育。\n2026-10-20 天然工况现场调查，受威胁人数为9人。\n本期潜在经济损失未知，未给出本期死亡人数。\n本期没有新的位移迹象；该描述不能回填2016年的发育状态。')
        elif d=='D04':
            _,y=page(c,'SYN-E滑坡天然工况现状',1,shared)
            text(c,y,'对象：SYN-E滑坡，整个滑坡体。\n本次天然工况现状发育程度专业结论：弱发育。\n本期现场登记受威胁人数8人，潜在经济损失未知。\n以下暴雨工况是预测假设，不能替换本页现状调查。')
            c.showPage();_,y=page(c,'SYN-E滑坡暴雨预测情景',2,'预测假设，非本期现状；本页所有结果均为假设情景数据。')
            text(c,y,'若出现持续极端暴雨，预测 SYN-E滑坡可能达到强发育状态；预测受威胁人数可能增至120人。\n假设模拟时点：2026-11-10；工况：暴雨。\n预测资料采用 GB/T 40112-2021 术语，不代表2026-10-20天然工况已经发生相应变化。\n工程建议：增设排水设施，并按现场复核意见调整方案。')
        elif d=='D05':
            _,y=page(c,'SYN-F滑坡甲组调查记录',1,shared)
            text(c,y,'甲组现场记录：SYN-F滑坡，整个滑坡体。\n按 GB/T 40112-2021 形成的发育程度结论为中等发育。\n受威胁人数为8人，潜在经济损失未调查。\n同日乙组调查在下一页，两组记录没有确认替代关系，应并列核查。')
            c.showPage();_,y=page(c,'SYN-F滑坡乙组调查记录',2,shared)
            text(c,y,'乙组现场记录：SYN-F滑坡，整个滑坡体。\n按 GB/T 40112-2021 形成的发育程度结论为中等发育。\n受威胁人数为120人，潜在经济损失未调查。\n该人数与甲组记录有矛盾；没有复核裁决，不宣布乙组数据覆盖甲组。')
        elif d=='D06':
            _,y=page(c,'SYN-G滑坡调查及损失统计',1,shared)
            text(c,y,'对象：SYN-G滑坡，整个滑坡体。\n发育程度专业结论为中等发育，采用 GB/T 40112-2021。\n受威胁人数未统计，应保持未知；危险性查表所需危害程度可使用另一有效指标。\n潜在经济损失统计采用亿元为单位，见横向附表。不得把“0.08亿元”直接当成“0.08万元”。')
            c.showPage();_,y=page(c,'SYN-G滑坡财产调查横向附表',2,shared,landscape=True)
            y=table(c,y,[['对象','统计时点','统计口径','数值','单位','备注'],['SYN-G滑坡',DATE,'潜在经济损失','0.08','亿元','未发生，险情估计'],['SYN-G滑坡',DATE,'受威胁人数','未统计','人','未知，不填零']], [105,100,150,75,80,268])
            text(c,y,'本表只表达本期潜在损失，不是历史直接经济损失。附表保留原单位；换算应记录转换过程。',width=778)
        elif d=='D07':
            _,y=page(c,'SYN-H滑坡现状调查表（扫描档）',1,shared)
            y=table(c,y,[['调查项目','现场记录'],['对象','SYN-H滑坡，整个滑坡体'],['发育程度（GB/T 40112-2021）','弱发育'],['受威胁人数','100 人'],['潜在经济损失','未知'],['诱发因素记录','降雨']], [235,296])
            text(c,y,'本页将转换成真实的图像PDF；没有隐藏文字层。用于核验本地OCR后文字识别链路。')
            c.showPage();_,y=page(c,'SYN-I滑坡现状人数区间调查',2,shared)
            text(c,y,'对象：SYN-I滑坡，整个滑坡体。\n按 GB/T 40112-2021 形成的发育程度专业结论：弱发育。\n受威胁人数为8～12人，是调查范围估计，不是选定人数8人或12人。\n潜在经济损失未知。人数区间跨越危害程度边界，应保留可能等级，不能取平均数10人代替原始区间。')
        elif d=='D08':
            _,y=page(c,'山地公路滑坡图文混合档案',1,shared)
            text(c,y,'本档案含 SYN-J 与 SYN-K 两处独立滑坡。\nSYN-J滑坡：本期受威胁人数20人。现场记录后缘有裂缝、前缘有季节性径流；没有形成按 GB/T 40112-2021 的发育程度结论。\nSYN-J滑坡照片页只作形态示意，不得从图中道路人数推算受威胁人口。\nSYN-K滑坡的原始报告附表见第2页，其“基本稳定、危害轻”为其他标准的原词，不等同本国标发育程度、危害程度。')
            c.showPage();_,y=page(c,'SYN-K滑坡原始附表（图像页）',2,shared)
            y=table(c,y,[['项目','原报告记录'],['对象','SYN-K滑坡，整个滑坡体'],['采用标准','DZ/T 0286-2015'],['稳定状态原词','基本稳定'],['危害程度原词','危害轻'],['计算稳定系数 Fs','0.98'],['受威胁人数（本期实测）','5 人'],['工程建议','防护设施设计容量120人']], [235,296])
            text(c,y,'原报告没有给出 GB/T 40112-2021 的发育程度结论，稳定系数和防护容量均不得补作该结论。')
            c.showPage();_,y=page(c,'SYN-J滑坡地形示意与观察记录',3,shared)
            c.setStrokeColor(colors.HexColor('#527c6b'));c.setLineWidth(2)
            p=c.beginPath();p.moveTo(55,390);p.lineTo(140,430);p.lineTo(250,590);p.lineTo(335,615);p.lineTo(410,560);p.lineTo(525,525);c.drawPath(p)
            c.setFillColor(colors.HexColor('#dbe9de'));c.rect(65,320,460,40,fill=1,stroke=0)
            c.setFillColor(colors.HexColor('#233f50'));c.setFont(font,12);c.drawString(95,335,'道路示意（不按比例）；虚构绘图，不含现场照片')
            text(c,285,'SYN-J滑坡观察：后缘局部裂缝，前缘湿润；未记录连续位移监测，缺少多项可复核观察。\n示意图没有独立发育程度结论。不得因为缺少完整描述就判为弱发育，也不得因裂缝一项就自动判为强发育。')
        c.showPage();c.save()
        dest=pdfdir/(d+'_'+doc['name']+'_隔离样例.pdf')
        documents[d]=str(dest.relative_to(OUT))

    expectations={
        'SYN-A':{'development':['中等发育'],'threatened_people':[9],'harm':'危害小','grade':'危险性小'},
        'SYN-B':{'development':['强发育'],'threatened_people':[120],'harm':'危害大','grade':'危险性大'},
        'SYN-C':{'development':['强发育'],'threatened_people':[10],'harm':'危害中等','grade':'危险性大'},
        'SYN-D':{'development':['弱发育'],'threatened_people':[9],'harm':'危害小','grade':'危险性小'},
        'SYN-E':{'development':['弱发育'],'threatened_people':[8],'harm':'危害小','grade':'危险性小'},
        'SYN-F':{'development':['中等发育'],'threatened_people':[8,120],'harm':None,'grade':None,'must_conflict':True},
        'SYN-G':{'development':['中等发育'],'potential_loss_wan':[800],'harm':'危害大','grade':'危险性大'},
        'SYN-H':{'development':['弱发育'],'threatened_people':[100],'harm':'危害中等','grade':'危险性中等'},
        'SYN-I':{'development':['弱发育'],'people_interval':[8,12],'harm':None,'grade':None,'possible_harm':['危害小','危害中等']},
        'SYN-J':{'development':[],'threatened_people':[20],'harm':'危害中等','grade':None},
        'SYN-K':{'development':[],'threatened_people':[5],'harm':'危害小','grade':None},
    }
    targets=[]
    for d in catalog():
        for key in d['cases']:
            targets.append({'case_id':key,'aliases':[key+'滑坡',key], 'document_ids':[d['id']],
                            'time':DATE,'scenario':'natural','scope_kind':'whole_landslide',
                            'source_standard':'GB/T 40112-2021'})
    dump(OUT/'资料目录.json',{'synthetic':True,'development_only':True,'documents':[{**d,'path':documents[d['id']]} for d in catalog()]})
    dump(OUT/'评估目标.json',{'synthetic':True,'purpose':'explicit requested assessment scopes; no grade answers','targets':targets})
    dump(OUT/'预先冻结的预期.json',{'synthetic':True,'cases':expectations,'model_receives_expected_answers':False})
    dump(OUT/'归档隔离标识.json',{'data_kind':'synthetic_open_development_fixture','archive_only':True,'automatic_ingestion_allowed':False,'independent_acceptance_allowed':False,'expert_validation':False})
    print('Created native fixture sources; raster materialization is a separate local phase',flush=True)


def materialize():
    import fitz
    from PIL import Image,ImageEnhance
    if (OUT/'冻结协议.json').exists():raise RuntimeError('Cannot change frozen PDFs')
    for doc in load(OUT/'资料目录.json')['documents']:
        with fitz.open(WORK/'native'/(doc['id']+'.pdf')) as source:
            result=fitz.open()
            for index,p in enumerate(source):
                raster=doc['id']=='D07' or (doc['id']=='D08' and index==1)
                if not raster:
                    result.insert_pdf(source,from_page=index,to_page=index);continue
                pix=p.get_pixmap(dpi=165,alpha=False)
                img=Image.open(io.BytesIO(pix.tobytes('png'))).convert('L')
                img=ImageEnhance.Contrast(img).enhance(0.87)
                if doc['id']=='D07' and index==1:img=img.rotate(0.6,resample=Image.Resampling.BICUBIC,expand=False,fillcolor=245)
                buffer=io.BytesIO();img.save(buffer,format='JPEG',quality=82)
                target=result.new_page(width=p.rect.width,height=p.rect.height)
                target.insert_image(target.rect,stream=buffer.getvalue())
            result.set_metadata({'title':doc['name']+' / SIMULATED ONLY','subject':'Synthetic development fixture; not engineering data','author':'Codex / SlopeKG'})
            result.save(OUT/doc['path'],garbage=4,deflate=True);result.close()
    print('Materialized 8 PDF files / 18 pages including 3 raster-only pages',flush=True)


def freeze():
    from slopekg.knowledge_settings import KnowledgeSettings
    from slopekg.model_client import ModelProfile
    if (OUT/'冻结协议.json').exists():raise RuntimeError('Already frozen')
    source_hashes={str(p.relative_to(ROOT)):sha(p) for p in sorted((ROOT/'src/slopekg').rglob('*.py'))}
    source_hashes['scripts/simulate_complex_pdf_assessment.py']=sha(Path(__file__))
    for p in (ROOT/'data/standards/GBT40112-2021').glob('*.json'):source_hashes[str(p.relative_to(ROOT))]=sha(p)
    profile=ModelProfile.load(ROOT/'config/models/deepseek-knowledge.json')
    assert profile.max_output_tokens==393216
    settings=KnowledgeSettings(max_calls=40,model_concurrency=3,review_mode='none',max_review_calls=0,
                               record_model_inputs=True,ocr_pages=4,max_transient_retries=1,transient_retry_budget=2)
    files=[OUT/x['path'] for x in load(OUT/'资料目录.json')['documents']]
    from slopekg import knowledge_prompts
    prompt_values={k:v for k,v in vars(knowledge_prompts).items() if k.isupper() and isinstance(v,str)}
    dump(OUT/'冻结协议.json',{'synthetic':True,'open_development':True,'hidden_set_used':False,
         'stage':'before_debugging','scope':'Knowledge recognition, isolated test bridge, existing standard service',
         'model_profile':profile.identity(),'knowledge_settings':asdict(settings),'maximum_requests_including_retries':40,
         'pdf_hashes':{str(p.relative_to(ROOT)):sha(p) for p in files},
         'expectations_sha256':sha(OUT/'预先冻结的预期.json'),'targets_sha256':sha(OUT/'评估目标.json'),
         'source_hashes':source_hashes,'full_prompt_constants':prompt_values,
         'request_input_recording':True,'post_change_model_retest':'only if recognition code is changed; local adapter replay is labeled separately'})
    with zipfile.ZipFile(OUT/'改前代码冻结.zip','w',zipfile.ZIP_DEFLATED) as z:
        for rel in source_hashes:z.write(ROOT/rel,rel)
    baseline_paths=load(ROOT/'outputs/20261031_模拟PDF国标流程/正式数据基线.json')
    dump(OUT/'正式数据本轮基线.json',{rel:sha(ROOT/rel) if (ROOT/rel).is_file() else None for rel in baseline_paths})
    print('Frozen 40-request total cap, 8 PDFs, full prompts and source snapshot',flush=True)


def recognize():
    from slopekg.knowledge_pipeline import KnowledgePipeline
    from slopekg.knowledge_settings import KnowledgeSettings
    from slopekg.model_client import CompatibleVisionClient,ModelProfile
    protocol=load(OUT/'冻结协议.json')
    if (OUT/'模型运行已启动.json').exists():raise RuntimeError('One-shot recognition already started; do not repeat billed/unknown requests')
    for rel,digest in protocol['source_hashes'].items():
        if sha(ROOT/rel)!=digest:raise RuntimeError('Code changed after freeze: '+rel)
    for rel,digest in protocol['pdf_hashes'].items():
        if sha(ROOT/rel)!=digest:raise RuntimeError('PDF changed after freeze')
    dump(OUT/'模型运行已启动.json',{'generation_started':True,'max_requests':40,'no_automatic_replay':True})
    client=CompatibleVisionClient(ModelProfile.load(ROOT/'config/models/deepseek-knowledge.json'),ROOT)
    pipeline=KnowledgePipeline(client,WORK/'knowledge',root=ROOT,
        settings=KnowledgeSettings(**protocol['knowledge_settings']),parsed_dir=WORK/'parsed',
        progress_callback=lambda pct,stage,msg:print(pct,stage,msg,flush=True))
    run=pipeline.run([ROOT/rel for rel in protocol['pdf_hashes']])
    dump(OUT/'隔离归档_识别全量审计.json',{'synthetic':True,'archive_only':True,'recognition_run':run})
    dump(OUT/'识别运行回执.json',{'synthetic':True,'summary':run['summary'],'receipts':run['receipts'],
                               'processing_status':run.get('processing_status')})
    # Full actual model messages, transport ledger and output are audit archives,
    # not a live recognition cache. Credentials are never present in these logs.
    with zipfile.ZipFile(OUT/'隔离归档_模型请求与原回复.zip','w',zipfile.ZIP_DEFLATED) as z:
        for p in (WORK/'knowledge').rglob('*'):
            if p.is_file() and p.suffix in {'.json','.jsonl','.txt'}:z.write(p,str(p.relative_to(WORK/'knowledge')))
    dump(OUT/'改前预测冻结.json',{'recognition_sha256':sha(OUT/'隔离归档_识别全量审计.json'),
                               'receipts_sha256':sha(OUT/'识别运行回执.json')})
    print(json.dumps(run['summary'],ensure_ascii=False),flush=True)


def legacy_map(fact):
    """The old test bridge, frozen for comparison, not production software."""
    from slopekg.standard_assessment.contract import enumeration,number
    field=fact.get('field','');value=str(fact.get('value',''))
    quotes=''.join(e.get('quote','') for e in fact.get('evidence',[]))
    if fact.get('status')=='rejected' or not quotes:return None
    if '发育' in field and value in ['强发育','中等发育','弱发育'] and value in quotes:
        return 'development',enumeration('development',value)
    if ('人数' in field or '人口' in field) and '威胁' in field and re.fullmatch(r'\s*\d+(?:\.0+)?\s*(?:人)?\s*',value):
        n=int(float(value.replace('人','').strip()))
        if str(n) in quotes:return 'threatened_people',number(n,'person')
    return None


def assess(stage='before'):
    if stage!='before':return assess_current(stage)
    from slopekg.config import PATHS
    from slopekg.standard_assessment import StandardAssessmentService
    from slopekg.standard_assessment.contract import CONTRACT_VERSION,fact_record,identity
    audit=load(OUT/'隔离归档_识别全量审计.json');run=audit['recognition_run']
    targets=load(OUT/'评估目标.json')['targets']
    docs={Path(d['path']).name.split('_',1)[0]:d for d in run['documents']}
    service=StandardAssessmentService(replace(PATHS,output_dir=WORK/stage))
    results=[];mapping=[]
    for target in targets:
        key=target['case_id']
        scope={'subject':{'source_object_ref':key},'time':{'kind':'instant','raw':target['time'],'start':target['time'],'end':target['time']},
               'scenario':{'code':target['scenario'],'raw':'天然工况','basis_refs':[]}}
        facts=[];contexts=[];sources=[]
        for did in target['document_ids']:
            doc=docs.get(did)
            if not doc:continue
            entities={e['id']:e['name'] for e in doc['entities']}
            for f in doc['facts']:
                owner=str(f.get('object',''))+' '+entities.get(f.get('entity_id'),'')
                if not any(a in owner for a in target['aliases']):continue
                mapped=legacy_map(f)
                if not mapped:continue
                variable,value=mapped
                src={'record_ref_id':identity('synsource_',f),'source_path':str(Path(doc['path']).relative_to(ROOT)),
                     'snapshot_sha256':doc['source_sha256'],'collection':'knowledge.documents.facts','record_id':f['id'],'original_record':f}
                ctx={'context_id':identity('syncontext_',f['evidence']),'source_path':src['source_path'],
                     'source_sha256':doc['source_sha256'],'spans':[{'quote':e['quote'],'file_page_1_based':e.get('page')} for e in f['evidence']],
                     'table_context':{},'synthetic':True}
                typed=fact_record(variable,value,scope,source_refs=[src['record_ref_id']],evidence_refs=[ctx['context_id']],
                    origin='knowledge',semantic='reported_judgment' if variable=='development' else 'observation',
                    role='observation',support='literal_matched',source_standard='GB/T 40112-2021')
                typed['synthetic']=True
                facts.append(typed);contexts.append(ctx);sources.append(src)
                mapping.append({'case':key,'fact_id':f['id'],'field':f.get('field'),'raw_value':f.get('value'),
                    'source_role':f.get('role'),'source_time':f.get('time'),'source_condition':f.get('condition'),
                    'typed_variable':variable,'typed_value':value,'quotes':[e['quote'] for e in f['evidence']]})
        case={'case_id':key,'contract_version':CONTRACT_VERSION,'target_ref':key,'scope':scope,'resolution_version':1,
              'fact_ids':[f['fact_id'] for f in facts],'context_ids':[c['context_id'] for c in contexts],
              'source_hashes':{src['source_path']:src['snapshot_sha256'] for src in sources},'source_coverage':'SYNTHETIC_OPEN_DEVELOPMENT',
              'hazard_type_supported':True,'selections':{},'synthetic':True}
        with service.store.transaction() as db:
            for table,rows in [('source_record',sources),('evidence_context',contexts),('fact_assertion',facts),('assessment_case',[case])]:
                for row in rows:service.store.put(table,row,db)
        result=service.evaluate_assessment({'contract_version':CONTRACT_VERSION,'case_id':key,'expected_resolution_version':1})
        results.append({'case':key,'facts':facts,'result':result,'database_readback':service.get_case(key)['case']['case_id']==key})
    dump(OUT/('隔离归档_'+stage+'_字段与评估.json'),{'synthetic':True,'bridge_version':'legacy-test-bridge-v1','mapping':mapping,'results':results})
    print(stage,'assessed',len(results),'cases',flush=True)


def assess_current(stage):
    from slopekg.config import PATHS
    from slopekg.standard_assessment import StandardAssessmentService
    from slopekg.standard_assessment.contract import CONTRACT_VERSION,fact_record,identity
    from slopekg.standard_assessment.knowledge_bridge import VERSION,candidate,compact,explicit_judgment_candidates
    run=load(OUT/'隔离归档_识别全量审计.json')['recognition_run']
    targets=load(OUT/'评估目标.json')['targets']
    docs={Path(d['path']).name.split('_',1)[0]:d for d in run['documents']}
    service=StandardAssessmentService(replace(PATHS,output_dir=WORK/stage))
    results=[];mapping=[];rejected=[]
    for target in targets:
        key=target['case_id']
        scope={'subject':{'source_object_ref':key},'time':{'kind':'instant','raw':target['time'],'start':target['time'],'end':target['time']},
               'scenario':{'code':target['scenario'],'raw':'天然工况','basis_refs':[]}}
        facts=[];contexts=[];sources=[];bindings=[]
        for did in target['document_ids']:
            doc=docs[did];page_texts={p['page']:'\n'.join(u['text'] for u in doc['units'] if u['page']==p['page']) for p in doc['pages']}
            local_targets=[t for t in targets if did in t['document_ids']]
            page_targets={p:{t['case_id'] for t in local_targets if any(compact(a) in compact(text) for a in t['aliases'])}
                          for p,text in page_texts.items()}
            original=list(doc['facts'])
            # Exact source retrieval is a separate, explicitly audited recovery
            # step. It is never mislabeled as another model prediction.
            for f in [*original,*explicit_judgment_candidates(doc['units'])]:
                mapped=candidate(f,case_scope=scope,target_key=key,aliases=target['aliases'],page_texts=page_texts,page_targets=page_targets)
                if not mapped['accepted']:
                    if mapped['reason']!='not_an_assessment_input':rejected.append({'case':key,'source_fact_id':f['id'],'reason':mapped['reason']})
                    continue
                variable=mapped['variable'];value=mapped['value'];fact_scope=mapped['scope']
                source_path=str(Path(doc['path']).relative_to(ROOT))
                source_origin=f.get('extraction_origin','knowledge_model_fact')
                src={'record_ref_id':identity('synsource_',f),'source_path':source_path,'snapshot_sha256':doc['source_sha256'],
                     'collection':'knowledge.documents.units' if source_origin!='knowledge_model_fact' else 'knowledge.documents.facts',
                     'record_id':f['id'],'original_record':f,'extraction_origin':source_origin}
                spans=[{'quote':e['quote'],'file_page_1_based':e.get('page')} for e in f['evidence']]
                spans += [{'quote':page_texts[p][:350],'file_page_1_based':p,'purpose':'binding_and_scope_context'} for p in mapped['source_pages']]
                ctx={'context_id':identity('syncontext_',{'fact':f['id'],'spans':spans}),'source_path':source_path,
                     'source_sha256':doc['source_sha256'],'spans':spans,'table_context':{},'synthetic':True}
                binding={'binding_id':identity('synbinding_',{'source':f['id'],'target':key}),
                         'source_object_ref':doc['id']+':'+str(f.get('entity_id') or f['id']),
                         'candidate_target_refs':[key],'selected_target_ref':key,'relation':'assertion_about',
                         'scope_limit':'源PDF内、显式请求对象的候选属性归属；不是跨资料物理体同一性确认',
                         'basis_refs':[ctx['context_id']],'mapping_status':'source_evidence_candidate',
                         'binding_basis':mapped['binding_basis'],'professional_confirmation':False,'version':VERSION}
                fact_scope['subject']['binding_id']=binding['binding_id']
                role=mapped['role']
                if role=='simulation':role='observation'  # Explicit isolated fixture policy only.
                semantic='reported_judgment' if variable=='development' else 'reported_estimate' if role=='reported_estimate' else 'reported_calculation' if variable=='fs' else 'observation'
                typed=fact_record(variable,value,fact_scope,source_refs=[src['record_ref_id']],evidence_refs=[ctx['context_id']],
                    origin='knowledge' if source_origin=='knowledge_model_fact' else 'source_retrieval',semantic=semantic,
                    role=role,support='literal_matched',source_standard=mapped['source_standard'],
                    feature='recognized_context' if variable=='geological_context' else None)
                typed.update(synthetic=True,basis_status=mapped['basis_status'],normalization_receipt={
                    'raw_value':f.get('value'),'raw_unit':f.get('unit'),'source_role':f.get('role'),
                    'transformations':mapped['transformations'],'source_origin':source_origin})
                facts.append(typed);contexts.append(ctx);sources.append(src);bindings.append(binding)
                mapping.append({'case':key,'fact_id':f['id'],'typed_fact_id':typed['fact_id'],'field':f.get('field'),'raw_value':f.get('value'),
                    'raw_unit':f.get('unit'),'source_role':f.get('role'),'source_time':f.get('time'),'source_condition':f.get('condition'),
                    'typed_role':role,'typed_variable':variable,'typed_value':value,'typed_scope':fact_scope,'basis_status':typed['basis_status'],
                    'source_origin':source_origin,'binding_basis':mapped['binding_basis'],'transformations':mapped['transformations'],
                    'quotes':[e['quote'] for e in f['evidence']]})
        case={'case_id':key,'contract_version':CONTRACT_VERSION,'target_ref':key,'scope':scope,'resolution_version':1,
              'fact_ids':[f['fact_id'] for f in facts],'context_ids':[c['context_id'] for c in contexts],
              'source_hashes':{str(Path(docs[did]['path']).relative_to(ROOT)):docs[did]['source_sha256'] for did in target['document_ids']},
              'source_coverage':'SYNTHETIC_OPEN_DEVELOPMENT_MODEL_PLUS_EXPLICIT_SOURCE_RETRIEVAL',
              'hazard_type_supported':True,'selections':{},'synthetic':True}
        with service.store.transaction() as db:
            for table,rows in [('source_record',sources),('evidence_context',contexts),('identity_binding',bindings),('fact_assertion',facts),('assessment_case',[case])]:
                for row in rows:service.store.put(table,row,db)
        result=service.evaluate_assessment({'contract_version':CONTRACT_VERSION,'case_id':key,'expected_resolution_version':1})
        results.append({'case':key,'facts':facts,'result':result,'database_readback':service.get_case(key)['case']['case_id']==key})
    dump(OUT/('隔离归档_'+stage+'_字段与评估.json'),{'synthetic':True,'bridge_version':VERSION,'mapping':mapping,'rejected_mappings':rejected,'results':results})
    print(stage,'assessed',len(results),'cases',flush=True)


def score(stage='before'):
    from slopekg.standard_assessment.contract import applicability
    expected=load(OUT/'预先冻结的预期.json')['cases']
    data=load(OUT/('隔离归档_'+stage+'_字段与评估.json'))
    scores=[]
    for row in data['results']:
        key=row['case'];exp=expected[key];result=row['result']
        scoped={'scope':result['assessment_scope']}
        facts=[f for f in row['facts'] if f['value'] is not None and applicability(f,scoped)['status']!='incompatible']
        actual_development=sorted({f['value']['label'] for f in facts if f['variable_code']=='development'})
        actual_people=sorted({f['value']['value'] for f in facts if f['variable_code']=='threatened_people' and f['value']['kind']=='number'})
        actual_loss=sorted({f['value']['value'] for f in facts if f['variable_code']=='potential_loss_wan' and f['value']['kind']=='number'})
        intervals=[f['value'] for f in facts if f['variable_code']=='threatened_people' and f['value']['kind']=='interval']
        # Correct null by accident is not an input-coverage pass.
        t15=result.get('partials',{}).get('table15') or {}
        checks={'development_inputs':actual_development==sorted(exp['development']),
                'harm_result':t15.get('harm')==exp['harm'],
                'grade_result':result['table17_result']['grade']==exp['grade'],'database_readback':row['database_readback'],
                'not_professionally_confirmed':result['review_status']=='pending' and not result['formal_assessment_enabled']}
        if 'threatened_people' in exp:checks['people_inputs']=actual_people==sorted(exp['threatened_people'])
        if 'potential_loss_wan' in exp:checks['loss_inputs']=actual_loss==exp['potential_loss_wan']
        if 'people_interval' in exp:checks['interval_input']=any([v['lower'],v['upper']]==exp['people_interval'] for v in intervals)
        if exp.get('must_conflict'):checks['conflict_retained']=bool(result.get('conflicts')) or t15.get('conflict',False)
        if 'possible_harm' in exp:checks['harm_branches']=set(t15.get('possible_grades',[]))==set(exp['possible_harm'])
        scores.append({'case':key,'passed':all(checks.values()),'checks':checks,'expected':exp,
                       'actual_development':actual_development,'actual_people':actual_people,'actual_loss':actual_loss,
                       'actual_harm':t15.get('harm'),'actual_grade':result['table17_result']['grade'],
                       'workflow_status':result.get('workflow_status')})
    dump(OUT/(stage+'_逐例核验.json'),{'synthetic':True,'recognition_predictions_sha256':sha(OUT/'隔离归档_识别全量审计.json'),
                                   'passed':sum(x['passed'] for x in scores),'total':len(scores),'cases':scores})
    print(json.dumps([{'case':x['case'],'passed':x['passed'],'failed':[k for k,v in x['checks'].items() if not v]} for x in scores],ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['prepare','materialize','freeze','recognize','assess','score'])
    parser.add_argument('--stage',default='before');args=parser.parse_args()
    if args.phase in {'assess','score'}:globals()[args.phase](args.stage)
    else:globals()[args.phase]()
