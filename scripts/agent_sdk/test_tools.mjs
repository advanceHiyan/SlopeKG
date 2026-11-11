import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';

const runtime=JSON.parse(await fs.readFile('.private/agent-runtime/runtime.json','utf8'));
process.env.SLOPEKG_DSH_MODULES=runtime.modules;
const base=path.resolve('outputs/20261107_第五路Agent/tool-tests/'+randomUUID());
const root=path.join(base,'workspace');
await fs.mkdir(path.join(root,'inputs'),{recursive:true});
await fs.mkdir(path.join(root,'outputs'));await fs.mkdir(path.join(base,'outside'));
process.env.SLOPEKG_AGENT_WORKSPACE=root;
process.env.SLOPEKG_AGENT_TOOL_AUDIT=path.join(base,'tool_calls.jsonl');
const {confined,apply,ALLOWED}=await import('./workspace_tools.mjs');
await assert.rejects(()=>confined(root,'../outside'),/outside/);
await assert.rejects(()=>confined(root,path.join(base,'outside')),/outside/);
await assert.rejects(()=>confined(root,'inputs/overwrite.txt',true),/Writes/);
await fs.symlink(path.join(base,'outside'),path.join(root,'escape'),'junction');
await assert.rejects(()=>confined(root,'escape/new.txt'),/escapes/);
const registered=[], guards=[],saved=[];
await apply({tools:{guard:f=>guards.push(f),register:t=>registered.push(t),schemas:()=>registered},
  attachments:{saveImage:async a=>{saved.push(a);return {id:'canary',mediaType:a.mediaType};}}});
assert.deepEqual(registered.map(t=>t.name),ALLOWED);
assert.ok(guards[0]({name:'bash'}));assert.equal(guards[0]({name:'view_image'}),undefined);
const req=createRequire(path.join(runtime.modules,'../package.json'));
const {default:sharp}=await import(pathToFileURL(req.resolve('sharp')).href);
await sharp({create:{width:40,height:20,channels:3,background:'#f00'}}).png().toFile(path.join(root,'inputs/page.png'));
const tool=registered.find(t=>t.name==='view_image');
await tool.execute({path:'inputs/page.png',left:0,top:0,width:20,height:10,scale:2},{});
assert.equal(saved.length,1);assert.equal((await sharp(saved[0].data).metadata()).width,40);
await assert.rejects(()=>tool.execute({path:'inputs/page.png',left:30,top:0,width:20,height:10},{}),/Invalid/);
if(process.argv[2]){
  process.env.SLOPEKG_ASSESSMENT_PYTHON=path.resolve(process.argv[2]);
  process.env.SLOPEKG_ASSESSMENT_PREVIEW_SCRIPT=path.resolve('scripts/agent_sdk/preview_assessment.py');
  const output=name=>path.join(root,'outputs',name);
  const page={document_id:'d',physical_page:1,status:'complete',remaining:[]};
  await fs.writeFile(path.join(root,'manifest.json'),JSON.stringify({documents:[{
    document_id:'d',original_filename:'synthetic.pdf',original_source_sha256:'0'.repeat(64),
    selected_physical_pages:[1],files:[{kind:'source_page_image',physical_page:1,path:'inputs/page.png'}]}]}));
  const fact={id:'f',document_id:'d',object:'o',parent_object:'',field:'threatened_people',value:5,unit:'person',
    condition:'',role:'observed',status:'ok',uncertainty:'',evidence:{physical_page:1,quote:'5人',locator:'synthetic'}};
  const saveFact=()=>fs.writeFile(output('facts.jsonl'),JSON.stringify(fact)+'\n');
  await fs.writeFile(output('objects.json'),JSON.stringify([{id:'o',document_id:'d',type:'landslide_body'}]));
  await fs.writeFile(output('coverage.json'),JSON.stringify({pages:[page]}));
  await fs.writeFile(output('report.md'),'synthetic document tool test');
  const input={id:'i',variable:'threatened_people',value:{kind:'number',value:5,unit:'person'},fact_ids:['f'],scope:'历史点位描述'};
  const submission={schema_version:'slopekg.agent-assessment/1',cases:[{id:'c',document_id:'d',object_id:'o',
    applicability:{landslide:true,reason:'source',basis_fact_ids:['f']},time:{kind:'unknown'},
    scenario:{code:'unspecified'},inputs:[input]}]};
  const saveSubmission=()=>fs.writeFile(output('assessment.json'),JSON.stringify(submission));
  await saveFact();await saveSubmission();
  const check=()=>registered.find(t=>t.name==='check_outputs').execute({},{});
  const textual=await check();
  assert.deepEqual(textual.errors,[]);assert.equal(textual.ok,true);
  assert.equal(textual.assessment.cases[0].inputs[0].selected,false);
  input.scope={};await saveSubmission();
  const trial=await check();
  assert.equal(trial.assessment.persisted_cases,0);
  assert.equal(trial.assessment.cases[0].table17.grade,null);
  assert.equal(trial.assessment.cases[0].inputs[0].selected,true);
  input.fact_ids=['foreign'];await saveSubmission();
  assert.equal((await check()).stage,'assessment');
  input.fact_ids=['f'];await saveSubmission();
  fact.evidence.document_id='foreign-pdf';await saveFact();
  assert.deepEqual((await check()).errors,['Fact/evidence document mismatch: f']);
  const {continueDelivery}=await import('./delivery_check.mjs');
  const spec={workspace:root,control_dir:base,task_prompt:'original',delivery_repair_prompt:'repair',
    assessment_python:process.env.SLOPEKG_ASSESSMENT_PYTHON,
    assessment_preview_script:process.env.SLOPEKG_ASSESSMENT_PREVIEW_SCRIPT};
  const prompts=[];
  await continueDelivery({spec,stopped:async()=>false,run:async prompt=>{
    prompts.push(prompt);
    if(prompts.length===2){delete fact.evidence.document_id;await saveFact();}
    return {finalResponse:prompt};
  }});
  assert.equal(prompts.length,2);
  assert.match(prompts[1],/Fact\/evidence document mismatch: f/);
  const receipt=JSON.parse(await fs.readFile(path.join(base,'delivery-repair.json'),'utf8'));
  assert.equal(receipt.status,'verified');
  assert.match(await fs.readFile(path.join(base,'delivery-repair-drafts','facts.jsonl'),'utf8'),/foreign-pdf/);
  const acceptable=[];
  await continueDelivery({spec,stopped:async()=>false,run:async prompt=>{acceptable.push(prompt);return {};}});
  assert.deepEqual(acceptable,['original']); // An evidence gap is a valid completed handoff.
  fact.evidence.document_id='foreign-pdf';await saveFact();
  const stillBlocked=[];
  await continueDelivery({spec,stopped:async()=>false,run:async prompt=>{stillBlocked.push(prompt);return {};}});
  assert.equal(stillBlocked.length,2);
  assert.equal(JSON.parse(await fs.readFile(path.join(base,'delivery-repair.json'),'utf8')).status,'still_blocked');
  const stopped=[];
  await continueDelivery({spec,stopped:async()=>true,run:async prompt=>{stopped.push(prompt);return {};}});
  assert.deepEqual(stopped,['original']);
  assert.equal((await fs.readdir(path.join(root,'scratch'))).filter(x=>x.startsWith('assessment-preview-')).length,0);
}
console.log(JSON.stringify({passed:true,checks:['traversal','absolute escape','input immutability','junction escape','tool guard','image crop and attachment','assessment feedback and foreign references','real evaluator preview when Python supplied'],runtime:runtime.expected_version,receipt:base}));
