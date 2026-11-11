import fs from 'node:fs/promises';
import {appendFileSync} from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {continueDelivery} from './delivery_check.mjs';

const spec=JSON.parse(await fs.readFile(process.argv[2],'utf8'));
const requireRuntime=createRequire(path.join(spec.modules,'..','package.json'));
const sdkManifest=JSON.parse(await fs.readFile(requireRuntime.resolve('@deepseek-ai/dsh-sdk-client/package.json'),'utf8'));
const runtimeManifest=JSON.parse(await fs.readFile(requireRuntime.resolve('@deepseek-ai/dsh/package.json'),'utf8'));
if(sdkManifest.version!==runtimeManifest.version)throw Error('SDK/runtime version mismatch: select a matching installation');
if(spec.expected_version && runtimeManifest.version!==spec.expected_version)throw Error('Runtime changed: recheck the candidate SDK and update the project runtime selection explicitly');
const {DeepSeekHarness}=await import(pathToFileURL(requireRuntime.resolve('@deepseek-ai/dsh-sdk-client')).href);
const plugin=path.join(path.dirname(fileURLToPath(import.meta.url)),'workspace_tools.mjs');
const model={id:spec.model,name:spec.model,contextWindow:1000000,maxTokens:spec.max_tokens,
  inputModalities:['text','image'],imagePixelBudget:4000000,imageMaxBytes:5242880};
const patch=[
  ...['persistent-bash','persistent-pwsh','str-replace-editor','terminal-bash','terminal-pwsh',
      'session-log-deepseek','plugin-package-inventory-deepseek'].map(id=>({id,disabled:true})),
  {id:'llm-deepseek',config:{apiKeyEnv:'DEEPSEEK_API_KEY',baseURL:spec.gateway_url,
    thinking:'enabled',reasoningEffort:spec.reasoning_effort,maxTokens:spec.max_tokens,
    defaultContextWindow:1000000,streamIdleTimeoutMs:600000,models:[model],
    maxInlineRequestImageBytes:167772160,maxRequestFilesBytes:167772160,maxImagesPerRequest:1000}},
  {id:'agent-spine',config:{includeHarnessIdentity:false,includeRuntimeContext:false,
    persona:spec.system_prompt,workspaceContext:false,skills:{enabled:false},toolBash:false,toolJobs:false,
    maxParallelToolCalls:4}},
  {insert:[{id:'slopekg-attachments',name:'@deepseek-ai/dsh-attachment-local',config:{dshHome:spec.dsh_home,
    normalizedImageMaxPixels:8000000,normalizedImageMaxBytes:8388608}},
    {id:'slopekg-document-tools',name:pathToFileURL(plugin).href}]},
];
const patchPath=path.join(spec.control_dir,'profile.patch.json');
await fs.writeFile(patchPath,JSON.stringify(patch,null,2));
const childEnv={};
for(const k of ['SystemRoot','WINDIR','COMSPEC','PATH','PATHEXT','SystemDrive','PROCESSOR_ARCHITECTURE',
  'NUMBER_OF_PROCESSORS','LANG','HTTPS_PROXY','HTTP_PROXY','NO_PROXY','SSL_CERT_FILE','SSL_CERT_DIR'])
  if(process.env[k])childEnv[k]=process.env[k];
Object.assign(childEnv,{DSH_HOME:spec.dsh_home,HOME:spec.user_home,USERPROFILE:spec.user_home,
  APPDATA:path.join(spec.user_home,'AppData/Roaming'),LOCALAPPDATA:path.join(spec.user_home,'AppData/Local'),
  XDG_CONFIG_HOME:path.join(spec.user_home,'config'),XDG_CACHE_HOME:path.join(spec.user_home,'cache'),
  TEMP:path.join(spec.control_dir,'temp'),TMP:path.join(spec.control_dir,'temp'),
  DEEPSEEK_API_KEY:process.env.SLOPEKG_GATEWAY_TOKEN,DEEPSEEK_BASE_URL:spec.gateway_url,
  DSH_MAX_TOKENS_AS_SUCCESS:'false',SLOPEKG_DSH_MODULES:spec.modules,
  SLOPEKG_AGENT_WORKSPACE:spec.workspace,SLOPEKG_AGENT_TOOL_AUDIT:path.join(spec.control_dir,'tool_calls.jsonl'),
  SLOPEKG_ASSESSMENT_PYTHON:spec.assessment_python,
  SLOPEKG_ASSESSMENT_PREVIEW_SCRIPT:spec.assessment_preview_script});
for(const dir of [childEnv.APPDATA,childEnv.LOCALAPPDATA,childEnv.TEMP,childEnv.XDG_CONFIG_HOME,childEnv.XDG_CACHE_HOME])
  await fs.mkdir(dir,{recursive:true});
const harness=new DeepSeekHarness({profile:'sdk-minimal',patches:[patchPath],
  dshHome:spec.dsh_home,cwd:spec.workspace,processCwd:spec.workspace,env:childEnv,
  provider:'deepseek-official',model:spec.model,reasoningEffort:spec.reasoning_effort,maxTokens:spec.max_tokens,
  initializeTimeoutMs:60000,requestTimeoutMs:60000,shutdownTimeoutMs:10000});
const compatibility={sdk_version:sdkManifest.version,runtime_version:runtimeManifest.version,
  public_api:'DeepSeekHarness.start/run/close',profile:'sdk-minimal',started_at:new Date().toISOString()};
let timedOut=false;
const deadline=spec.timeout_seconds ? setTimeout(()=>{timedOut=true;harness.close().catch(()=>{});},spec.timeout_seconds*1000) : null;
let stopping=false;
const stopWatcher=setInterval(async()=>{
  if(stopping)return;
  try{await fs.access(path.join(spec.control_dir,'stop.request.json'));}catch{return;}
  stopping=true;
  await harness.close().catch(()=>{});
},500);
try{
  await harness.start();
  const audit=(await fs.readFile(path.join(spec.control_dir,'tool_calls.jsonl'),'utf8')).trim().split('\n').map(JSON.parse);
  const registration=audit.find(e=>e.event==='registered');
  if(!registration || registration.allowed.length!==7)throw Error('Document-tool capability probe failed');
  compatibility.tools=registration.allowed;
  compatibility.handshake='passed';
  await fs.writeFile(path.join(spec.control_dir,'compatibility.json'),JSON.stringify(compatibility,null,2));
  if(spec.preflight_only){console.log(JSON.stringify({status:'ready',...compatibility}));}
  else{
    const result=await continueDelivery({spec,run: prompt=>harness.run(prompt,{sessionId:spec.session_id,onNotification:n=>{
      appendFileSync(path.join(spec.control_dir,'events.jsonl'),JSON.stringify(n)+'\n');
    }}),stopped: async()=>{
      if(stopping||timedOut)return true;
      try{await fs.access(path.join(spec.control_dir,'stop.request.json'));return true;}
      catch(error){if(error.code!=='ENOENT')throw error;return false;}
    }});
    console.log(JSON.stringify({status:'idle',session_id:result.sessionId,final_response:result.finalResponse}));
  }
}catch(e){
  await fs.writeFile(path.join(spec.control_dir,'sdk_error.txt'),String(e.stack??e));
  console.error(String(e.message??e));process.exitCode=1;
}finally{
  clearTimeout(deadline);
  clearInterval(stopWatcher);
  try{await harness.close();}catch(e){process.exitCode=1;console.error('SDK cleanup failed: '+e.message);}
}
