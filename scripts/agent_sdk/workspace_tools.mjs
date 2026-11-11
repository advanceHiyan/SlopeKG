// A bounded tool set for the DSH agent. No shell, arbitrary code, network or
// personal-session tools are exposed. Reading/cropping/search strategy is the agent's.
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import {randomUUID} from 'node:crypto';
import {checkDelivery} from './delivery_check.mjs';

const requireRuntime = createRequire(path.join(process.env.SLOPEKG_DSH_MODULES, '..', 'package.json'));
const {defineTool} = await import(pathToFileURL(requireRuntime.resolve('@deepseek-ai/dsh-tools')).href);
const {default: sharp} = await import(pathToFileURL(requireRuntime.resolve('sharp')).href);
export const name = 'slopekg-document-tools';
export const inject = ['tools', 'attachments'];
export const ALLOWED = ['list_files','read_text','search_text','view_image','write_text','write_json','check_outputs'];

export async function confined(root, name, write=false) {
  if(typeof name!=='string' || !name || name.includes('\0'))throw Error('Invalid workspace path');
  const absolute=path.resolve(root,name);
  const inside=p=>p===root || p.startsWith(root+path.sep);
  if(!inside(absolute))throw Error('Path is outside this task workspace');
  let probe=absolute;
  while(true){
    try{if(!inside(await fs.realpath(probe)))throw Error('Link escapes this task workspace');break;}
    catch(e){if(e.code!=='ENOENT')throw e;const up=path.dirname(probe);if(up===probe)throw e;probe=up;}
  }
  if(write && !['outputs','scratch'].some(d=>absolute.startsWith(path.join(root,d)+path.sep)))
    throw Error('Writes are limited to outputs/ and scratch/; inputs are immutable');
  return absolute;
}

export async function apply(ctx) {
  const root=path.resolve(process.env.SLOPEKG_AGENT_WORKSPACE);
  const audit=process.env.SLOPEKG_AGENT_TOOL_AUDIT;
  const record=entry=>fs.appendFile(audit,JSON.stringify({time:new Date().toISOString(),...entry})+'\n');
  ctx.tools.guard(exec=>ALLOWED.includes(exec.name)?undefined:'This recognition agent only has task-scoped document tools');
  const jsonOutput={schema:{type:'json'},render:(_args,value)=>[{type:'text',text:JSON.stringify(value)}]};
  function register(name,description,parameters,execute,output=jsonOutput,readOnly=true){
    ctx.tools.register(defineTool({name,description,parameters,output,isConcurrencySafe:()=>readOnly,
      async execute(args,exec){
        try{const result=await execute(args,exec);await record({tool:name,args,status:'ok'});return result;}
        catch(e){await record({tool:name,args,status:'error',error:String(e.message)});throw e;}
      }}));
  }
  const str=(description,required=true)=>({type:'string',...(required?{required:true}:{}),description});
  const integer=(description,required=false)=>({type:'integer',...(required?{required:true}:{}),description});
  register('list_files','List files in this task workspace. No access to other projects or personal DSH state.',
    {path:str('Relative directory; use . for root',false)},async a=>{
      const p=await confined(root,a.path||'.');
      return (await fs.readdir(p,{withFileTypes:true})).map(d=>({name:d.name,type:d.isSymbolicLink()?'link':d.isDirectory()?'directory':'file'}));
    });
  register('read_text','Read a UTF-8 text or JSON file with explicit line pagination. Original text is auxiliary; inspect page images for layout and tables.',
    {path:str('Workspace-relative file'),start_line:integer('1-based first line'),line_count:integer('Number of lines, at most 400')},async a=>{
      const p=await confined(root,a.path);const lines=(await fs.readFile(p,'utf8')).split(/\r?\n/);
      const start=a.start_line??1,count=a.line_count??200;
      if(start<1||count<1||count>400)throw Error('Invalid line range');
      return {path:a.path,total_lines:lines.length,start_line:start,lines:lines.slice(start-1,start-1+count),
        next_line:start-1+count<lines.length?start+count:null};
    });
  register('search_text','Literal search across the provided text files; returns matching lines and locations. Choose files from the manifest.',
    {query:str('Nonempty literal text'),paths:{type:'array',required:true,items:{type:'string'}},offset:integer('Result offset')},async a=>{
      if(!a.query||a.paths.length>100)throw Error('Invalid search');const matches=[];
      for(const file of a.paths){const p=await confined(root,file);const lines=(await fs.readFile(p,'utf8')).split(/\r?\n/);
        lines.forEach((line,i)=>{if(line.includes(a.query))matches.push({path:file,line:i+1,text:line});});}
      const offset=a.offset??0;if(offset<0)throw Error('Invalid offset');
      return {total:matches.length,matches:matches.slice(offset,offset+100),next_offset:offset+100<matches.length?offset+100:null};
    });
  register('view_image','View an original page or a precise crop. Coordinates are pixels of the original file. Crop and enlarge as needed for dense tables, graph legends and formulas; no fixed reading order.',
    {path:str('Workspace-relative PNG/JPEG/WebP'),left:integer('Crop left pixel'),top:integer('Crop top pixel'),width:integer('Crop width'),height:integer('Crop height'),scale:{type:'number',description:'Optional crop enlargement, 1 to 4'}},async a=>{
      const p=await confined(root,a.path);const info=await sharp(p).metadata();let im=sharp(p);let renderedPath=a.path;
      const hasCrop=['left','top','width','height'].some(k=>a[k]!==undefined);
      if(hasCrop){if(!['left','top','width','height'].every(k=>Number.isInteger(a[k]))||a.left<0||a.top<0||a.width<1||a.height<1||a.left+a.width>info.width||a.top+a.height>info.height)throw Error('Invalid original-image crop rectangle');im=im.extract({left:a.left,top:a.top,width:a.width,height:a.height});}
      const scale=a.scale??1;if(scale<1||scale>4)throw Error('Scale must be 1 to 4');
      if(scale!==1)im=im.resize({width:Math.round((a.width??info.width)*scale)});
      const data=await im.png().toBuffer();
      if(hasCrop||scale!==1){renderedPath='scratch/crop-'+randomUUID()+'.png';const target=await confined(root,renderedPath,true);await fs.mkdir(path.dirname(target),{recursive:true});await fs.writeFile(target,data);}
      const ref=await ctx.attachments.saveImage({data,mediaType:'image/png',name:renderedPath});
      return {path:a.path,rendered_path:renderedPath,original_width:info.width,original_height:info.height,
        crop:hasCrop?{left:a.left,top:a.top,width:a.width,height:a.height}:null,image:ref};
    },{schema:{type:'json'},render:(_a,v)=>[{type:'text',text:JSON.stringify({...v,image:{...v.image}})},
      {type:'image',attachment:v.image}]});
  register('write_text','Write or append a complete text file under outputs/ or scratch/. Split large deliverables into explicit batches instead of truncating them.',
    {path:str('Output path'),text:str('Complete text for this write'),append:{type:'boolean'}},async a=>{
      const p=await confined(root,a.path,true);await fs.mkdir(path.dirname(p),{recursive:true});
      await fs.writeFile(p,a.text,{encoding:'utf8',flag:a.append?'a':'w'});return {path:a.path,bytes:Buffer.byteLength(a.text),append:!!a.append};
    },jsonOutput,false);
  register('write_json','Write a JSON artifact, or append records to JSONL. For facts.jsonl use jsonl=true and supply an array of complete facts; append=true retains previous batches.',
    {path:str('Output path'),value:{type:'json',required:true},jsonl:{type:'boolean'},append:{type:'boolean'}},async a=>{
      if(a.append&&!a.jsonl)throw Error('Append is only supported for JSONL');
      const p=await confined(root,a.path,true);await fs.mkdir(path.dirname(p),{recursive:true});
      const text=a.jsonl?(Array.isArray(a.value)?a.value:[a.value]).map(v=>JSON.stringify(v)).join('\n')+'\n':JSON.stringify(a.value,null,2)+'\n';
      await fs.writeFile(p,text,{encoding:'utf8',flag:a.append?'a':'w'});return {path:a.path,bytes:Buffer.byteLength(text),append:!!a.append};
    },jsonOutput,false);
  register('check_outputs','Run the final recognition admission checks and the read-only assessment trial. Fix only issues supported by the original pages. True missing evidence, conflicts and inapplicability are valid results.',{},async()=>{
    const checked=await checkDelivery({python:process.env.SLOPEKG_ASSESSMENT_PYTHON,
      script:process.env.SLOPEKG_ASSESSMENT_PREVIEW_SCRIPT,workspace:root,audit});
    const folder=path.join(path.dirname(audit),'assessment-previews');
    await fs.mkdir(folder,{recursive:true});
    const file=randomUUID()+'.json';
    await fs.writeFile(path.join(folder,file),JSON.stringify(checked,null,2)+'\n');
    return {...checked,receipt:'control/assessment-previews/'+file};
  },jsonOutput,false);
  const tools=ctx.tools.schemas().map(t=>t.name);
  if(ALLOWED.some(name=>!tools.includes(name)))throw Error('Required document tools are unavailable');
  await record({event:'registered',workspace:root,tools,allowed:ALLOWED,isolation:'curated tools; canonical-path checks; no arbitrary shell or code'});
}
