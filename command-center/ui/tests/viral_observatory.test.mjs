import {test,beforeEach} from 'node:test';
import assert from 'node:assert/strict';
import * as trace from '../api_trace.js';
import {buildVfxCommand} from '../pages/viral_vfx_panel.js';
import {graphData} from '../pages/observatory.js';
import {api,setCsrf,clearCsrf} from '../api.js';
const routes=[{path:'/api/video-studio/commands',methods:['POST']},{path:'/api/tasks/{task_id}',methods:['GET']}];
beforeEach(()=>{trace.resetTraceSession();globalThis.localStorage={getItem:()=>'',setItem(){},removeItem(){}};});
test('disabled by default and redacts concrete IDs/query',()=>{
 assert.equal(trace.traceBegin('GET','/api/tasks/PRIVATE?token=SECRET'),null);
 trace.setTraceRoutes(routes);trace.setTraceEnabled(true);
 const t=trace.traceBegin('GET','/api/tasks/PRIVATE?token=SECRET','PRIVATE');trace.traceEnd(t,200,'HTTP_OK');
 const out=JSON.stringify(trace.traceSnapshot());assert(!out.includes('PRIVATE'));assert(!out.includes('SECRET'));assert(out.includes('/api/tasks/{task_id}'));assert(out.includes('NOT_INSTRUMENTED'));
});
test('auth, egress, provider and external routes not recorded',()=>{
 trace.setTraceEnabled(true);
 for(const p of ['/api/login','/api/providers/12','/api/studio/egress/confirm','https://example.com'])assert.equal(trace.traceBegin('POST',p),null);
});
test('session change drops pending tickets and history',()=>{
 trace.setTraceRoutes(routes);trace.setTraceEnabled(true);const t=trace.traceBegin('GET','/api/tasks/1');
 setCsrf('synthetic');trace.traceEnd(t,200,'HTTP_OK');assert.deepEqual(trace.traceSnapshot().rows,[]);assert.equal(trace.traceSnapshot().enabled,false);
 trace.setTraceEnabled(true);trace.traceEnd(trace.traceBegin('GET','/api/tasks/1'),200,'HTTP_OK');clearCsrf();assert.equal(trace.traceSnapshot().rows.length,0);
});
test('bounded in-memory ring, unknown route not retained',()=>{
 trace.setTraceEnabled(true);
 for(let n=0;n<140;n++)trace.traceEnd(trace.traceBegin('GET','/api/PRIVATE_'+n),200,'HTTP_OK');
 assert.equal(trace.traceSnapshot().rows.length,120);assert(!JSON.stringify(trace.traceSnapshot()).includes('PRIVATE_'));
});
test('real API wrapper keeps errors, method and measured status without body',async()=>{
 trace.setTraceRoutes(routes);trace.setTraceEnabled(true);let calls=0;
 globalThis.fetch=async()=>{calls++;return new Response(JSON.stringify({error:{message:'synthetic failure'}}),{status:409});};
 await assert.rejects(api.raw('/api/video-studio/commands',{method:'POST',body:{secret:'NEVER_TRACE'},traceOrigin:'VideoStudio.command.apply'}));
 const row=trace.traceSnapshot().rows[0];assert.equal(row.status,409);assert.equal(row.ui_action,'VideoStudio.command.apply');assert.equal(row.outcome,'HTTP_ERROR');assert.equal(calls,1);assert(!JSON.stringify(row).includes('NEVER_TRACE'));
});
test('aborted request is not HTTP success',async()=>{
 trace.setTraceEnabled(true);globalThis.fetch=async()=>{throw new DOMException('aborted','AbortError');};
 await assert.rejects(api.raw('/api/tasks/1'));const row=trace.traceSnapshot().rows[0];assert.equal(row.outcome,'ABORTED');assert.equal(row.status,null);
});
const project=()=>({id:'p',revision:2,active_sequence_id:'s',media:{m:{has_video:true}},sequences:[{id:'s',tracks:[{id:'v',kind:'video',locked:false,clips:[{id:'c',media_id:'m'}]},{id:'a',kind:'audio',locked:false,clips:[{id:'ac',media_id:'m'}]}]}]});
test('VFX plan uses canonical commands and preserves caller project',()=>{
 const p=project(),before=structuredClone(p);const c=buildVfxCommand(p,['c'],{preset:'phonk_hard',intensity:.5,bpm:140,offset:0},true);
 assert.deepEqual(p,before);assert.equal(c.type,'timeline.apply');assert.equal(c.operations.length,3);assert.equal(c.operations[0].effect.type,'viral_vfx');assert.equal(c.operations[2].end,15000000);
});
for(const ids of [[],['unknown'],['ac'],['c','c']])test('invalid VFX selection '+JSON.stringify(ids),()=>assert.throws(()=>buildVfxCommand(project(),ids,{})));
test('locked track is rejected',()=>{const p=project();p.sequences[0].tracks[0].locked=true;assert.throws(()=>buildVfxCommand(p,['c'],{}));});
test('graph has only actual relationships and no invented agent cooperation',()=>{
 const d={tasks:[{id:1,agent_id:7,status:'running'}],brain:{runs:[{id:2,task_id:1,model_id:5,model_kind:'local',status:'running'}],tool_calls:[{id:3,run_id:2,tool:'read',status:'executed'}]},memory:{facts:[]}};
 const g=graphData(d,'agents');assert.deepEqual(g.edges,[{from:'a7',to:'t1'},{from:'t1',to:'r2'},{from:'r2',to:'m5'},{from:'r2',to:'c3'}]);
 const empty=graphData({...d,tasks:[],brain:{runs:[],tool_calls:[]}},'agents');assert.equal(empty.nodes.length,0);
});
test('memory only draws recorded provenance and supersession',()=>{
 const d={memory:{facts:[{id:1,source_run_id:9,superseded_by:2},{id:2,source_run_id:9,superseded_by:null}]}};
 const g=graphData(d,'memory');assert(g.edges.some(e=>e.from==='f1'&&e.to==='f2'));assert.equal(g.nodes.length,3);
});
