"""Build specific-information walkthroughs from preserved open run artifacts.

Every model excerpt below is selected from a saved input/cache answer. Analysis
is explicitly separate. Does not import recognition code or send any requests.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/知联调试'


def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def canonical(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))


class Saved:
    def __init__(self,key):
        self.data=read(OUT/f'assets/work_audit/{key}/data.json')
        self.doc=self.data['document'];self.units={u['id']:u for u in self.doc['units']}
        self.entities={e['id']:e for e in self.doc['entities']}
        self.calls=[{**c,'saved':read(OUT/c['raw_url'])} for c in self.data['calls']]
    def sources(self,ids):return [self.units[k] for k in ids]
    def stage(self,name,source=None):
        return [c for c in self.calls if c['stage']=='knowledge_'+name and (source is None or source in c['span_ids'])]
    def context(self,c):return c['saved']['context']
    def answer(self,c):return c['saved']['cache'].get('answer',{})
    def raw_entities(self,c,ids):
        names={self.entities[e]['name'] for e in ids}
        return [e for e in self.answer(c).get('entities',[]) if e.get('name') in names]
    def assignments(self,ids):
        result=[]
        for call in self.stage('attribute'):
            for chunk in self.answer(call).get('assignments',[]):
                selected=[e for e in chunk.get('entities',[]) if e.get('entity_id') in ids]
                if selected:result.append({'receipt_ordinal':call['ordinal'],'chunk_id':chunk.get('chunk_id'),'entities':selected})
        return result
    def dossier(self,ids):
        return [{'entity':{k:e.get(k) for k in ['id','name','kind','description','unit_ids','chunk_ids']},
            'sources':self.sources(e.get('unit_ids',[]))} for key in ids if (e:=self.entities[key])]
    def subjects(self,c,ids):
        ctx=self.context(c);values=ctx.get('subjects') or ([ctx['subject']] if ctx.get('subject') else [])
        return [s for s in values if s.get('id') in ids]
    def objects(self,c,ids):return [o for o in self.answer(c).get('objects',[]) if o.get('entity_id') in ids]
    def fact(self,key):return next(f for f in self.doc['facts'] if f['id']==key)
    def chunks(self,ids):return [c for c in self.doc['chunks'] if set(ids).intersection(c['unit_ids'])]
    def chunk_definition(self,chunk):
        # Final run chunks also contain later attribution results. Those are
        # deliberately excluded from the earlier segmentation step.
        return {k:chunk[k] for k in ['id','unit_ids','pages','parent_chunk_id'] if k in chunk}
    def call_refs(self,calls):
        return [{k:c.get(k) for k in ['key','detail_id','ordinal','stage','prompt_chars','input_saved',
                    'requested_max_output_tokens','finish_reason','detail_url','raw_url','usage']}
                for c in calls]
    def step(self,name,title,explanation,before,after,*,verdict='kept',units=(),calls=(),check=None):
        return {'name':name,'title':title,'explanation':explanation,'before':before,'after':after,
            'verdict':verdict,'source_units':self.sources(units),'calls':self.call_refs(calls),'check':check or {}}
    def story(self,key,title,question,conclusion,steps,ids):
        return {'id':key,'title':title,'question':question,'conclusion':conclusion,
            'dataset_key':self.data['key'],'kind':self.data['kind'],
            'source_sha256':self.doc['source_sha256'],'source_pdf':self.data['source_pdf'],
            'page_images':self.data['page_images'],'source_pages':self.doc['source_page_count'],
            'processed_pages':[p['page'] for p in self.doc['pages']],
            'recognizer':self.data['run_metadata'].get('recognizer'),
            'settings':self.data['run_metadata'].get('settings'),
            'producer':self.data['run_metadata'].get('producer'),
            'steps':steps,'entity_ids':ids,
            'units':self.doc['units'],
            'entity_names':{e['id']:e['name'] for e in [*self.doc['entities'],*self.doc.get('references',[])]},
            'source_dataset_json':f'assets/work_audit/{self.data["key"]}/data.json'}


def g209_height():
    s=Saved('g209-six');unit='p21b8t0';specific='e_b62e6818a1fb6e88';generic='e_00be0ec4be838f47'
    ids=[specific,generic];source=s.units[unit]
    discover=s.stage('discover',unit)[0];attribute=s.stage('attribute',unit)[0]
    synth_specific=next(c for c in s.stage('synthesize') if s.subjects(c,[specific]))
    synth_generic=next(c for c in s.stage('synthesize') if s.subjects(c,[generic]))
    relevant_chunk=s.chunks([unit])[0]
    chunk_answer=next(c for c in s.answer(attribute)['assignments'] if c['chunk_id']==relevant_chunk['id'])
    selected=[k for e in chunk_answer['entities'] for k in e['span_ids']]
    final=[f for f in s.doc['facts'] if any(e['span_id']==unit for e in f.get('evidence',[]))]
    assert '28' in source['text'] and unit not in selected
    assert unit not in s.entities[specific]['unit_ids'] and unit in s.entities[generic]['unit_ids']
    assert unit not in synth_specific['span_ids'] and unit in synth_generic['span_ids']
    assert not final
    raw_specific=s.objects(synth_specific,[specific]);raw_generic=s.objects(synth_generic,[generic])
    assert not any(e.get('span_id')==unit for obj in raw_generic for f in obj.get('facts',[]) for e in f.get('evidence',[]))
    stages=[
      s.step('原件','这处危岩体的最大坡高，原文写了28m',
        '物理第21页，第5.1节是具体工点。关键段落同时给出长度约60m、最大坡高约28m。追踪目标是这条坡高，不是整页事实数量。',
        {'question':'K2388+976-K2389+036左侧危岩体的最大坡高，最终去了哪里？'},
        {'original_source':source['text'],'physical_page':21},units=['p21b4t0',unit]),
      s.step('解析','这里是原生PDF文字，不是OCR漏字',
        '该次实际入链单元保留了完整的“最大坡高约28m”，并保留原页位置。此处没有识字错误这一证据。',
        {'page_record':next(p for p in s.doc['pages'] if p['page']==21)},source,units=[unit]),
      s.step('分块','第21页这句话进入了混合第12、21、22页的c0002',
        '内容没有在字符切分时消失。但一个初始块同时包含前面章节的第12页与具体工点的第21、22页，后续仍需逐段正确分配主体。',
        s.sources(['p21b4t0',unit]),s.chunk_definition(relevant_chunk),units=[unit],
        check={'target_in_chunk':unit in relevant_chunk['unit_ids'],'chunk_pages':relevant_chunk['pages']}),
      s.step('对象发现','模型此时已经认对具体对象，也已经读懂28m',
        '保存的发现响应把这句话列为具体工点的mentions，description也写了长约60m、最大坡高约28m。说明信息在这一阶段仍完整。模型返回id为空，程序随后生成本地对象ID；两者不能当同一份原始JSON。',
        {'source_span_in_saved_request':source},s.raw_entities(discover,[specific]),units=[unit],calls=[discover],
        check={'specific_identity_found':True,'height_source_cited_in_discovery':True}),
      s.step('归属','归属响应把c0002标完成，却没选这句给具体工点',
        '这份c0002归属答复选中的来源全来自第12页，并把具体工点列进unresolved。第21页的坡高来源未被选中。输入里有，输出的具体对象绑定里没有：第一次明显断链发生在这里。',
        {'target_source_in_attribute_request':source,'registry_subject':s.entities[specific]['name']},chunk_answer,
        verdict='lost',units=[unit],calls=[attribute],
        check={'target_in_request':True,'target_selected_in_return':False,'chunk_returned_status':chunk_answer['attribution_status'],
               'selected_source_pages':sorted({s.units[k]['page'] for k in selected})}),
      s.step('对象资料包','后续实际资料包：具体工点没拿到，泛称“危岩体”拿到了',
        '以保存的本地对象unit_ids及实际重组请求核对：具体工点资料包没有这条来源；泛称“危岩体”的199条来源里有它。这里展示的是最终形成的本地绑定，不把它伪称为上一阶段模型直接返回。',
        {'discovery_had_bound_this_source_to':s.entities[specific]['name']},
        {'specific_subject_sent':s.subjects(synth_specific,[specific]),'generic_subject_sent':s.subjects(synth_generic,[generic]),
         'target_in_generic_source_spans':source},verdict='misrouted',units=[unit],calls=[synth_specific,synth_generic],
        check={'specific_dossier_has_target':False,'generic_dossier_has_target':True,'generic_dossier_units':len(s.entities[generic]['unit_ids'])}),
      s.step('知识重组','具体工点没收到坡高原文，泛称大包也没返回坡高候选',
        '分别查看两个实际重组响应：具体工点的响应没有最大坡高；泛称包虽然收到这条来源，也没有返回引用它的事实。一个是资料包错配，另一个是收到后重组未覆盖，不能合成“模型没读到”。',
        {'specific_allowed_sources':s.subjects(synth_specific,[specific]),'generic_received_target':True},
        {'specific_object_response':raw_specific,'generic_object_response':raw_generic},
        verdict='lost',units=[unit],calls=[synth_specific,synth_generic]),
      s.step('校验','没有坡高候选进入门控，不能说是校验把28m拒绝了',
        '最终事实中没有任何一条引用p21b8t0。门控只能校验已返回的候选；这条没有形成候选，因此没有它的拒绝记录可供解释。',
        {'target_source_id':unit},final,verdict='absent',units=[unit],
        check={'final_facts_citing_target_source':len(final),'specific_height_gate_record_present':False}),
      s.step('最终结果','发现摘要里有28m，最终具体对象事实里却没有',
        '这正是要查的流程损失：原文→发现阶段都正确，归属阶段漏选；具体对象包缺来源，泛称包重组又未覆盖。此处没有新模型重测，也不据旧样例宣称当前算法已修复。',
        {'discovery_specific_description':s.raw_entities(discover,[specific])[0]['description']},
        {'specific_object_final_facts':[f for f in s.doc['facts'] if f['entity_id']==specific],
         'final_facts_citing_height_source':final},verdict='lost',units=[unit])]
    return s.story('g209-height','真实G209：28m坡高怎么丢了','发现时已认出28m，为什么具体危岩体的最终事实里没有？',
        '第一次断链在归属；随后进入泛称大包，又在重组中遗漏。不是OCR漏字，也不是最后门控拒绝。',stages,ids)


def syn_i():
    s=Saved('D07');unit='p2ocr4t0';target='e_c1454973a7f910be';topic='e_5241cd9e5187c2cd';ids=[target,topic]
    discover=s.stage('discover')[0];attribute=s.stage('attribute')[0];synth=s.stage('synthesize')[0]
    source=s.units[unit];raw_topic=s.raw_entities(discover,[topic])[0]
    assert any(m['span_id']==unit for m in raw_topic['mentions'])
    assert unit in discover['span_ids'] and unit in attribute['span_ids'] and unit not in synth['span_ids']
    assert not any(unit in e['unit_ids'] for e in s.doc['entities'])
    mapping=[m for m in s.data['extra']['downstream_mapping'] if m['case']=='SYN-I' and m['source_origin']=='explicit_source_statement_retrieval']
    assert mapping and mapping[0]['fact_id']=='literal_judgment_'+unit
    steps=[
      s.step('原件','SYN-I第2页原文明确写了“弱发育”',
        '这是虚构隔离资料，非真实工程结论。要追踪的是SYN-I这一处的“弱发育”，不能拿第1页SYN-H同样的“弱发育”代替。',
        {'target':'SYN-I滑坡的本期发育程度'},source,units=['p2ocr3t0',unit]),
      s.step('OCR','OCR单元里完整保留了这句话',
        '这里展示当时实际进入知联的规范化OCR单元，归档未保存OCR引擎原始JSON。值、对象邻近行和坐标都存在，不是OCR漏字。',
        {'original_physical_page':2},s.sources(['p2ocr1t0','p2ocr3t0',unit,'p2ocr5t0']),units=[unit]),
      s.step('分块','两页75条OCR全部在同一个c0001',
        '这条没有被切到另一个chunk，也没有超出输入预算。它与SYN-I对象行在同一块中；不能把后续丢失归咎于没有跨块上下文。',
        {'target_source':source},s.chunk_definition(s.doc['chunks'][0]),units=[unit],
        check={'chunk_count':len(s.doc['chunks']),'target_in_chunk':unit in s.doc['chunks'][0]['unit_ids']}),
      s.step('对象发现','发现阶段已读到SYN-I这一处弱发育',
        '“滑坡发育程度评价”概念的description写“两处结论均为弱发育”，mentions同时引用了SYN-H和SYN-I的来源。SYN-I具体对象也被发现，但它自己的mentions仅是标题/对象行。',
        {'target_in_discover_request':source},s.raw_entities(discover,ids),units=[unit],calls=[discover]),
      s.step('归属','归属请求有这句话，返回绑定没有它',
        '实际返回只给发育评价概念选了第1页H的弱发育；没有给SYN-I或这个概念选p2ocr4t0。发现时已经知道的来源，没有被后续绑定保留下来。',
        {'target_in_attribute_request':source},s.answer(attribute),verdict='lost',units=[unit],calls=[attribute],
        check={'target_in_saved_request':True,'target_in_any_final_owner_unit_ids':False}),
      s.step('对象资料包','重组输入只给SYN-I标题与对象行',
        '具体对象allowed_span_ids没有发育结论；概念包也只有H的弱发育来源。重组source_spans合集中根本没有p2ocr4t0，不能指望后面的模型凭描述替它补造证据。',
        {'sources_before_attribution':s.sources(['p2ocr3t0',unit])},
        {'subjects_sent':s.subjects(synth,ids),'source_spans_sent':s.context(synth)['source_spans']},
        verdict='lost',units=[unit],calls=[synth],check={'target_in_synthesis_request':False}),
      s.step('知识重组','模型明确回复：SYN-I结论无原文证据',
        '看保存的对象响应和unresolved：模型只能返回SYN-I名称、范围等；概念响应也明确说SYN-I的发育程度结论无原文证据。它此时确实没收到那条来源。',
        {'target_source_sent':False},s.objects(synth,ids),verdict='absent',units=[unit],calls=[synth]),
      s.step('校验','SYN-I弱发育没有候选，因此没有该条门控',
        '最终存在的弱发育事实引用第1页H的p1ocr7t0，scope也是SYN-H，不能算SYN-I被识别成功。SYN-I缺的是候选，不能用别人的source_aligned替代。',
        {'wanted_source':unit},[f for f in s.doc['facts'] if '发育' in f['field']],verdict='absent',units=['p1ocr7t0',unit]),
      s.step('最终/补检','评估接入后来补回，不等于原识别成功',
        'explicit_source_statement_retrieval从原p2ocr4t0补出弱发育，补检事实ID以literal_judgment开头。原模型预测没有因此改变。图上把这条补救单独列出，避免用最终评估结果掩盖前面归属丢失。',
        {'original_model_facts_citing_p2ocr4t0':[f for f in s.doc['facts'] if any(e['span_id']==unit for e in f.get('evidence',[]))]},mapping,
        verdict='recovered_downstream',units=[unit])]
    return s.story('syn-i','扫描SYN-I：弱发育在哪里掉链','OCR、发现都读到了，为何重组模型说没有原文？',
        '归属漏选了已经发现的来源，资料包把结论丢掉；后续原文补检才补回。',steps,ids)


def syn_c():
    s=Saved('D02');target='e_5ddb700a42192c7b';development='e_960b96640fa23900';people='e_690934cfc694be1e';ids=[target,development,people]
    sourceids=['p1b4t0','p3table0r0t0','p3table0r3t0'];fdev=s.fact('k_26649a80ba6aea5b8f1c93');fpeople=s.fact('k_ff0a7a966f6664e1a7e968')
    discover=s.stage('discover')[0];attribute=s.stage('attribute')[0];synth=[c for c in s.stage('synthesize') if s.subjects(c,ids)]
    mapping=[m for m in s.data['extra']['downstream_mapping'] if m['fact_id'] in [fdev['id'],fpeople['id']]]
    assert fdev['status']=='source_aligned' and fpeople['quality_issues']==['unit_basis_not_in_cited_source']
    steps=[
      s.step('原件','第1页给10人，第3页附表给强发育',
        '目标是把同一个SYN-C的两项跨页信息组织起来。不是把“第1页的人数”和“第3页的结论”当作两个互不相干对象。',
        {'target':'SYN-C滑坡'},s.sources(sourceids),units=['p1b4t0','p3table0r3t0']),
      s.step('解析/表格','两项值都被提取，表格结论保留表头关系',
        '第1页是原生文字；第3页结论是pdf_table行，带header_ids。原文已经有“10 人”，不是OCR没有单位。',
        {'pages':[p for p in s.doc['pages'] if p['page'] in [1,3]]},s.sources(sourceids),units=['p1b4t0','p3table0r3t0']),
      s.step('分块','3页77条、2538字符，仍然只有一个chunk',
        '它是跨页资料整合的例子，但不是跨chunk压力测试：两项内容实际在同一初始块。',
        {'unit_characters':sum(len(u['text']) for u in s.doc['units'])},s.chunk_definition(s.doc['chunks'][0]),units=['p3table0r3t0']),
      s.step('对象发现','具体滑坡与“发育程度”“人数”概念分别被发现',
        '这一步确实找到了SYN-C；同时把发育评价、人数作为独立概念。后续必须看scope和证据才能判断这些概念事实属于哪处滑坡，不能只看对象名称。',
        {'source_rows':s.sources(sourceids)},s.raw_entities(discover,ids),units=['p3table0r3t0'],calls=[discover]),
      s.step('归属','发育结论归给评价概念，人数归给人数概念',
        '具体SYN-C资料包和两个主题包拿到的来源并不相同。这里把模型返回的绑定及本地最终资料包分别展示，不把同chunk误认为同对象已完整收集。',
        {'registry_objects':[s.entities[k]['name'] for k in ids]},s.assignments(ids),verdict='split_across_subjects',units=['p3table0r3t0'],calls=[attribute]),
      s.step('对象资料包','跨页信息存在于不同主题资料包',
        '查看allowed_span_ids及完整来源：之后模型依靠明确scope把事实指回SYN-C；不是它们已经都挂在具体滑坡资料包下。',
        {'target_source_ids':sourceids},{'dossiers':s.dossier(ids),'subjects_sent':[v for c in synth for v in s.subjects(c,ids)]},units=['p3table0r3t0'],calls=synth),
      s.step('知识重组','两项值都返回了；人数还带了一个错误形态的unit_basis',
        '保存的重组结果把scope写成SYN-C，并提取强发育、10人。但是unit_basis被写成解释文字“原文‘10 人’”，而不是通常的单位计量基准。后续校验按字段契约处理它。',
        {'source_rows':s.sources(sourceids)},[o for c in synth for o in s.objects(c,ids)],units=['p1b4t0','p3table0r3t0'],calls=synth),
      s.step('校验','强发育通过字面对齐，10人因unit_basis被标待审',
        '人数的value=10和unit=人都在引文里，保存的质量问题却是unit_basis_not_in_cited_source。此例暴露的是模型字段填法与校验契约衔接问题，不应解释成原文缺人数。',
        {'source':'受威胁人数为 10 人，详见后附清点记录；潜在经济损失尚未统计。'},[fdev,fpeople],
        verdict='pending',units=['p1b4t0','p3table0r3t0']),
      s.step('最终/接入','下游按明确scope绑定SYN-C，保留原候选状态',
        '保存的接入mapping采用两条模型事实，binding_basis为explicit_fact_subject；typed_scope明确SYN-C。这个转换没有把原模型记录的pending改成识别已通过，也不代表专业签认。',
        {'original_final_facts':[fdev,fpeople]},mapping,verdict='mapped_downstream',units=['p3table0r3t0'])]
    return s.story('syn-c','跨页SYN-C：强发育与10人如何合上','跨页值都提到了，为什么一条通过、一条待审？',
        '跨页信息被分到主题对象后，依靠scope在下游重新绑定；10人另外卡在unit_basis契约。',steps,ids)


def syn_ab():
    s=Saved('D01');a='e_23383fa1a9150cb5';b='e_c7b1c367e888b0a4';ids=[a,b]
    sourceids=['p1table0r0t0','p1table0r2t0','p1table0r3t0'];discover=s.stage('discover')[0];attribute=s.stage('attribute')[0]
    synth=[c for c in s.stage('synthesize') if s.subjects(c,ids)]
    facts=[s.fact(k) for k in ['k_892a06204664092f181306','k_73d0db8d1d4c1d944c8ca0','k_e4776e7e15d844db879043','k_9e378fd99d646f870175e1']]
    assert all(set(sourceids)<=set(s.entities[k]['unit_ids']) for k in ids)
    mapping=[m for m in s.data['extra']['downstream_mapping'] if m['fact_id'] in {f['id'] for f in facts}]
    steps=[
      s.step('原件','同一张表里，A是9人，B是120人',
        '这是一组正向对照：检查模型能否按照对象列和行把值分开，而不是只看某个数是否出现。',
        {'wanted':{'SYN-A':['中等发育','9人'],'SYN-B':['强发育','120人']}},s.sources(sourceids),units=['p1table0r2t0','p1table0r3t0']),
      s.step('表格解析','两行是独立来源，但坐标框仍是整个表格',
        '实际单元包含cells、row、header_ids，足以保留行结构；两行的bbox都来自整张表，不是精确到单行的定位框，界面不能把同一个大框当行级精度证明。',
        {'original_page':1},s.sources(sourceids),units=['p1table0r2t0','p1table0r3t0']),
      s.step('分块','两页50条来源组成一个chunk',
        '它没有按A、B预切。后面仍需从同一块/同一表中正确识别不同对象。',
        {'source_ids':sourceids},s.chunk_definition(s.doc['chunks'][0]),units=['p1table0r2t0','p1table0r3t0']),
      s.step('对象发现','A、B两个具体对象分别建立身份',
        '看保存的发现响应，名称和引用对象行分别出现；后续本地才生成不同对象ID。另有主题概念，但不在这一步用总数量判断好坏。',
        s.sources(sourceids),s.raw_entities(discover,ids),units=['p1table0r2t0','p1table0r3t0'],calls=[discover]),
      s.step('归属','实际给A和B都分配了A/B两行',
        '这说明归属允许来源集合较宽，没有在此阶段把不同数据行严格隔开。接下来必须靠重组模型再核对对象列，不能把“有了对象ID”当成行归属已经完成。',
        {'A_row':'p1table0r2t0','B_row':'p1table0r3t0'},s.assignments(ids),verdict='broad_assignment',units=['p1table0r2t0','p1table0r3t0'],calls=[attribute]),
      s.step('对象资料包','实际请求里A、B各自的允许来源仍同时含两行',
        '这是宽资料包在实际发送前的状态，不是示意结构。共享来源合批时正文可以只传一份，但每个对象的allowed_span_ids仍需检查。',
        {'sources':s.sources(sourceids)},{'subjects_sent':[v for c in synth for v in s.subjects(c,ids)]},units=['p1table0r2t0','p1table0r3t0'],calls=synth),
      s.step('知识重组','这一例重组正确按行返回A=9、B=120',
        '保存的实际响应包含对应对象的发育程度和人数。这里应保留成功分支作为对照，不能只挑失败样例。成功也不证明上一步宽归属在所有表格中都可靠。',
        {'source_rows':s.sources(sourceids)},[o for c in synth for o in s.objects(c,ids)],units=['p1table0r2t0','p1table0r3t0'],calls=synth),
      s.step('校验','四条关键事实均引用对应行及必要表头',
        '检查每条事实的object、scope、value、unit与evidence，不能只看状态标签。下面保留全部字段与质量问题。',
        {'A_expected':['中等发育','9人'],'B_expected':['强发育','120人']},facts,units=['p1table0r2t0','p1table0r3t0']),
      s.step('最终/接入','A/B没有串值，下游采用对应四条模型事实',
        '这是同表多对象处理成功的具体证据；范围只限这张表和这四项，不延伸为整文档准确率或其他对象已正确。',
        {'original_final_facts':facts},mapping,verdict='kept',units=['p1table0r2t0','p1table0r3t0'])]
    return s.story('syn-ab','同表A/B：9人与120人有没有串','同一表中两个对象的资料，究竟在哪一步分开的？',
        '归属包仍较宽，但这次重组按对象列正确分出了四条关键事实；保留这个成功对照。',steps,ids)


def build_stories():
    stories=[g209_height(),syn_i(),syn_c(),syn_ab()]
    tracked={'g209-height':['p21b8t0'],'syn-i':['p2ocr4t0'],
             'syn-c':['p1b4t0','p3table0r3t0'],'syn-ab':['p1table0r2t0','p1table0r3t0']}
    for story in stories:story['tracked_source_ids']=tracked[story['id']]
    payload={'generated_from':'saved open run artifacts only','new_model_requests':0,
        'provider_raw_http_available':False,'stories':stories}
    text=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
    target=OUT/'assets/process_stories';target.mkdir(parents=True,exist_ok=True)
    (target/'data.json').write_text(text,encoding='utf-8')
    (target/'data.js').write_text('window.KNOWLEDGE_PROCESS_STORIES='+text.replace('<','\\u003c')+';',encoding='utf-8')
    (OUT/'process_story_receipt.json').write_text(json.dumps({'stories':[{'id':s['id'],'steps':len(s['steps']),
       'source_sha256':s['source_sha256'],'steps_with_saved_requests':sum(bool(t['calls']) for t in s['steps'])} for s in stories],
       'new_model_requests':0,'artifact_sha256':hashlib.sha256(text.encode()).hexdigest(),
       'prediction_or_algorithm_changed':False},ensure_ascii=False,indent=2),encoding='utf-8')
    return payload


if __name__=='__main__':
    sys=__import__('sys');sys.stdout.reconfigure(encoding='utf-8')
    p=build_stories();print([(s['id'],len(s['steps'])) for s in p['stories']])
