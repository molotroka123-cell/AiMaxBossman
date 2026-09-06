import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';

// Execute the unmodified production module with isolated DOM/API ports.
// Private exports are test instrumentation, not replacements for save logic.
async function load(raw) {
  const context=vm.createContext({console, URLSearchParams, setTimeout, clearTimeout,
    window:{addEventListener(){}}, document:{activeElement:null},
    localStorage:{getItem(){return null;},setItem(){},removeItem(){}}, Blob});
  function h(tag,...args) {
    const attrs=args[0] && !Array.isArray(args[0]) && typeof args[0]==='object' && !args[0].tag ? args.shift():{};
    return {tag,attrs,children:args.flat(Infinity),value:attrs.value||'',classList:{add(){},remove(){}},
      addEventListener(){},append(){},setAttribute(){}};
  }
  const stubs={
    '../api.js':{api:{raw}},
    '../components.js':{h,toastOk(){},toastError(){},confirmDialog(){},fmtDateShort(){},
      debounce(fn,ms){return (...a)=>setTimeout(()=>fn(...a),ms);}},
    './_ui.js':{pageHead:(title,sub,opts)=>({title,opts}),panel:(...a)=>h('panel',...a),
      btn:(label,fn)=>({label,fn}),pill:()=>({}),tag:()=>({}),field:()=>({})},
    './web_designer_viewport.js':{VIEWPORT_PRESETS:[],VIEWPORT_ZOOMS:[],VIEWPORT_LIMITS:{},
      viewportSettings(){return {};},loadViewport(){return {};},saveViewport(){},viewportGeometry(){return {};}}
  };
  const source=await readFile(new URL('../ui/pages/web_designer.js',import.meta.url),'utf8');
  const mod=new vm.SourceTextModule(source+'\nexport const probe={state,flushSave,head,page:WebDesignerPage,setEditor:n=>{editorNode=n;}};', {context});
  await mod.link((id)=>{const obj=stubs[id];assert.ok(obj,id);return new vm.SyntheticModule(Object.keys(obj),function(){for(const[k,v]of Object.entries(obj))this.setExport(k,v);},{context});});
  await mod.evaluate();
  const p=mod.namespace.probe;
  Object.assign(p.state,{id:1,meta:{id:1,version:1},code:'old',dirty:true,projects:[{id:1,name:'A'},{id:2,name:'B'}]});
  p.setEditor({value:'draft'});
  return p;
}

function response(id=1,version=2){return {ok:true,meta:{id,version}};}

test('late save acknowledgement cannot erase edits typed during the request',async()=>{
  const calls=[];let release;
  const p=await load(async(url,options)=>{calls.push({url,...options.body});if(calls.length===1)return new Promise(r=>release=r);return response(1,3);});
  const node={value:'first'};p.setEditor(node);
  const saving=p.flushSave(); await Promise.resolve();
  node.value='second';p.state.dirty=true;release(response());
  await saving;
  assert.equal(calls.length,2);assert.equal(calls[1].html,'second');assert.equal(calls[1].base_version,2);
  assert.equal(p.state.code,'second');assert.equal(p.state.dirty,false);
});

test('failed save refuses project switch and keeps the original draft',async()=>{
  const p=await load(async()=>{throw new Error('409 conflict');});let refreshes=0;
  const head=p.head({refresh(){refreshes++;}});const selector=head.opts.actions[0];selector.value='2';
  await selector.attrs.onChange();
  assert.equal(p.state.id,1);assert.equal(selector.value,'1');assert.equal(refreshes,0);assert.equal(p.state.dirty,true);
});

test('simultaneous Ctrl+S calls serialize the same pending save',async()=>{
  let calls=0,release;
  const p=await load(async()=>{calls++;return new Promise(r=>release=r);});
  const one=p.flushSave();const two=p.flushSave();await Promise.resolve();assert.equal(calls,1);
  release(response());await Promise.all([one,two]);assert.equal(p.state.dirty,false);
});

test('an old project response cannot overwrite the destination metadata',async()=>{
  let release;
  const p=await load(async()=>new Promise(r=>release=r));const work=p.flushSave();await Promise.resolve();
  p.state.id=2;p.state.meta={id:2,version:9};p.setEditor({value:'destination'});release(response());
  await work;assert.equal(p.state.meta.id,2);assert.equal(p.state.meta.version,9);assert.equal(p.state.dirty,true);
});

test('new-project action opens creation even when older projects exist',async()=>{
  const urls=[];const p=await load(async(url)=>{urls.push(url);if(url.endsWith('/templates'))return {items:[],palettes:[]};
    if(url.endsWith('/projects'))return {items:[{id:1,name:'existing'}]};throw new Error('old project selected');});
  p.state.dirty=false;
  const ctx={refresh(){},navigate(){}};const header=p.head(ctx);await header.opts.actions.find(x=>x.label==='+ Проект').fn();
  await p.page.render(ctx,{});
  assert.ok(!urls.some(u=>u.endsWith('/projects/1')));assert.equal(p.state.id,null);
});
