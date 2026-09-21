// Actual create-form handler with explicit DOM/API doubles, not a live browser.
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';

function mount() {
  const nodes=[], calls=[], failures=[];
  const h=(tag,...parts)=>{
    const node={tag,children:[],props:{},handlers:{},value:'',disabled:false,
      classList:{add(){},remove(){}},addEventListener(k,fn){this.handlers[k]=fn;}};
    if(parts[0] && typeof parts[0]==='object' && !Array.isArray(parts[0]) && !parts[0].tag) {
      Object.assign(node.props,parts.shift());
      node.value=node.props.value??'';
    }
    node.children=parts.flat(Infinity);nodes.push(node);return node;
  };
  const ctx={refresh(){}};
  const state={mutating:false,creating:true};
  const context={h,state,styleNode:()=>null,recoveryPanel:()=>null,pageHead:()=>null,
    panel:(title,body)=>body,
    btn:(label,handler)=>h('button',{onClick:handler},label),
    api:{raw:(url,options)=>new Promise((resolve,reject)=>calls.push({url,options,resolve,reject}))},
    operationError:async(e)=>failures.push(e),toastOk(){},LAST_KEY:'fixture',localStorage:{setItem(){}}};
  const path=process.env.WD_CREATE_SOURCE || new URL('../pages/web_designer.js',import.meta.url);
  const source=readFileSync(path,'utf8');
  const start=source.indexOf('function emptyState(ctx, catalog) {');
  const end=source.indexOf('/* ---------------- страница',start);
  assert(start>=0&&end>start,'real create handler must be present');
  vm.runInNewContext(source.slice(start,end)+'\nglobalThis.openCreate=emptyState;',context,{timeout:1000});
  context.openCreate(ctx,{items:[{id:'landing',title:'Template',hint:'Offline'},
    {id:'ai_local',title:'ИИ: творческий бриф (локально)',hint:'Local only'}],palettes:['indigo']});
  const submit=nodes.find(n=>n.tag==='button'&&n.children.includes('Открыть проект'));
  assert(submit);
  return {nodes,calls,failures,state,submit,click:()=>submit.props.onClick({currentTarget:submit})};
}

test('a second click does not create a duplicate project while first request is pending',async()=>{
  const t=mount();const first=t.click();const second=t.click();
  assert.equal(t.calls.length,1);assert.equal(t.submit.disabled,true);
  t.calls[0].resolve({meta:{id:7}});await Promise.all([first,second]);
  assert.equal(t.state.id,7);assert.equal(t.submit.disabled,false);assert.equal(t.state.mutating,false);
});

test('model/server failure leaves creation available for an explicit new attempt',async()=>{
  const t=mount();const first=t.click();t.calls[0].reject(new Error('fixture failure'));await first;
  assert.equal(t.failures.length,1);assert.equal(t.state.mutating,false);assert.equal(t.submit.disabled,false);
  const retry=t.click();assert.equal(t.calls.length,2);t.calls[1].resolve({meta:{id:8}});await retry;
  assert.equal(t.state.id,8);
});

test('the actual catalog card reaches the production create endpoint with ai_local',async()=>{
  const t=mount();
  const card=t.nodes.find(n=>n.tag==='button.bd-tpl'&&n.children.some(c=>c?.tag==='b'&&c.children.includes('ИИ: творческий бриф (локально)')));
  assert(card);card.handlers.click();
  const pending=t.click();
  assert.equal(t.calls[0].url,'/api/web-designer/projects');
  assert.equal(t.calls[0].options.method,'POST');assert.equal(t.calls[0].options.body.template,'ai_local');
  t.calls[0].resolve({meta:{id:9}});await pending;
});
