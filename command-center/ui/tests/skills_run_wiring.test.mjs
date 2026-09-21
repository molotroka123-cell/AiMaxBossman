// Executes the real page's modal/submission handler with an explicit fake DOM/API.
// This is a wiring regression, not a live browser or model acceptance.
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {collectSkillInputs, createSkillField} from '../skill_inputs.js';
class Element {
  constructor(kind='', attrs={}) {Object.assign(this,attrs);this.kind=kind;this.value=attrs.value??'';this.children=[];this.isConnected=true;}
  appendChild(child){this.children.push(child);return child;}
  addEventListener(){}
  set textContent(value){this.children=[];}
}
async function mount(id,schema) {
  const fields={}, calls=[], errors=[];let submit;
  const h=(kind,...args)=>new Element(kind,args[0]!==null&&typeof args[0]==='object'&&!Array.isArray(args[0])?args[0]:{});
  const input=attrs=>new Element('input',attrs);
  const textarea=attrs=>new Element('textarea',attrs);
  const select=(options,attrs={})=>new Element('select',{value:options[0]?.value??'',...attrs});
  const field=(label,el)=>{fields[label.replace(/ \*$/,'')]=el;return el;};
  const source=readFileSync(process.env.SKILLS_PAGE_SOURCE || new URL('../pages/skills.js',import.meta.url),'utf8')
    .replace(/^import[\s\S]*?;\n/gm,'').replace(/export default SkillsPage;?/,'');
  const context={collectSkillInputs,createSkillField,h,input,textarea,select,field,
    pick:(obj,keys,fallback)=>keys.map(k=>obj[k]).find(v=>v!==undefined)??fallback,
    api:{raw:async(path,opts)=>{if(!opts)return{frontmatter:{input_schema:schema}};calls.push({path,opts});return{task_id:7};}},
    openModal:()=>({body:new Element(),footer:new Element(),close(){}}),
    actionButton:(label,fn)=>{submit=fn;return new Element();},
    ui:{},icon:()=>new Element(),idVal:v=>v?Number(v):null,
    toast:message=>errors.push(message),toastOk:()=>{},toastError:e=>errors.push(e.message),
    confirmDialog:()=>false};
  vm.runInNewContext(source+'\nglobalThis.openRun = openRunSkill;',context,{timeout:1000});
  await context.openRun({state:{agents:[{id:1,name:'local test'}]},navigate(){}},{id});
  return{fields,calls,errors,submit};
}
const news={type:'object',required:['mode'],properties:{mode:{type:'string',enum:['supplied','search']},articles:{type:'array'},limit:{type:'integer',minimum:1},query:{type:'string'}},allOf:[{if:{properties:{mode:{const:'search'}}},then:{required:['query']}}]};
test('actual modal posts typed news input, omits blank optional, keeps draft agent null',async()=>{
  const t=await mount('open-news',news);
  t.fields.mode.value='supplied';t.fields.articles.value='[{"url":"https://example.com","title":"Test"}]';t.fields.limit.value='5';
  await t.submit();assert.deepEqual(t.errors,[]);assert.equal(t.calls.length,1);
  const body=t.calls[0].opts.body;assert(Array.isArray(body.input.articles));assert.equal(body.input.limit,5);
  assert.equal('query' in body.input,false);assert.equal(body.agent_id,null);
});
test('actual modal refuses invalid JSON before API POST',async()=>{
  const t=await mount('open-news',news);t.fields.mode.value='supplied';t.fields.articles.value='[bad';
  await t.submit();assert.equal(t.calls.length,0);assert.equal(t.errors.length,1);
});
test('actual modal enforces conditional query before API POST',async()=>{
  const t=await mount('open-news',news);t.fields.mode.value='search';
  await t.submit();assert.equal(t.calls.length,0);assert.equal(t.errors.length,1);
});
test('actual mimik modal has multiline textarea and posts one alternative',async()=>{
  const t=await mount('mimik',{properties:{markdown:{type:'string'},snapshot:{type:'object'}},oneOf:[{required:['markdown']},{required:['snapshot']}]});
  assert.equal(t.fields.markdown.kind,'textarea');
  t.fields.markdown.value='# Guide\n\n---\n\n## Step 01: Click Models';
  await t.submit();assert.equal(t.calls.length,1);assert.equal('snapshot' in t.calls[0].opts.body.input,false);
});
