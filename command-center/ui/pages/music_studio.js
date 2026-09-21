import { api } from '../api.js';
import { h, toastOk, toastError } from '../components.js';
import { errorBanner } from './_shared.js';
let state={prompt:'dark aggressive phonk, distorted cowbell melody, punchy 808 bass, Memphis-inspired drums, instrumental',lyrics:'[inst]',duration:90,bpm:130,model:'acestep-v15-turbo',taskId:'',task:null,health:null,error:null};
const MusicPage={id:'music-studio',title:'Music Studio',icon:'activity',nav:'primary',section:'studio',async render(ctx){
 try{state.health=await api.raw('/api/music/health');state.error=null}catch(e){state.error=e}
 const presets=await api.raw('/api/music/presets').catch(()=>({items:[]}));
 return h('div.stack.lg',h('div.page-head',h('div',h('h1','Music Studio'),h('div.dim','Локальная генерация музыки · ACE-Step 1.5')),h('span.badge',state.health?.status||'UNKNOWN')),state.error?errorBanner(state.error,ctx):null,
 h('section.panel',h('div.panel-head',h('h2','Стиль')),h('div.panel-body',h('div.row.wrap',...(presets.items||[]).map(p=>h('button.btn.btn-sm',{type:'button',onClick:()=>{state.prompt=p.prompt;state.bpm=p.bpm;ctx.refresh()}},p.label))),field('Описание',h('textarea.input',{value:state.prompt,onInput:e=>state.prompt=e.target.value,rows:5})),h('div.row',field('BPM',h('input.input',{type:'number',min:40,max:240,value:state.bpm,onInput:e=>state.bpm=Number(e.target.value)})),field('Длина, сек',h('input.input',{type:'number',min:10,max:600,value:state.duration,onInput:e=>state.duration=Number(e.target.value)}))),field('Lyrics / instrumental',h('textarea.input',{value:state.lyrics,onInput:e=>state.lyrics=e.target.value,rows:4})),h('button.btn.btn-primary',{type:'button',onClick:()=>generate(ctx)},'Сгенерировать трек'))),state.taskId?taskPanel(ctx):null)},
 onEvent(ev){return String(ev.kind||'').startsWith('music.')}};
function field(label,node){return h('label.stack.xs',h('div.small.dim',label),node)}
function taskPanel(ctx){return h('section.panel',h('div.panel-head',h('h2','Текущий трек')),h('div.panel-body',h('div.small','Task: '+state.taskId),h('div.small','Статус: '+(state.task?.status||'queued')),h('div.row',h('button.btn.btn-sm',{type:'button',onClick:()=>refreshTask(ctx)},'Обновить'),state.task?.status==='completed'?h('button.btn.btn-primary',{type:'button',onClick:()=>save(ctx)},'Сохранить локально'):null)))}
async function generate(ctx){try{const out=await api.raw('/api/music/generate',{method:'POST',body:{prompt:state.prompt,lyrics:state.lyrics,duration:state.duration,bpm:state.bpm,model:state.model,thinking:true}});state.taskId=out.task_id;state.task=out;toastOk('Музыка поставлена в очередь');ctx.refresh()}catch(e){toastError(e,'Генерация музыки не запущена')}}
async function refreshTask(ctx){try{state.task=await api.raw('/api/music/tasks/'+encodeURIComponent(state.taskId));ctx.refresh()}catch(e){toastError(e,'Не удалось получить статус')}}
async function save(ctx){try{const out=await api.raw('/api/music/tasks/'+encodeURIComponent(state.taskId)+'/save',{method:'POST'});toastOk('Трек сохранён: '+out.path);state.task=out;ctx.refresh()}catch(e){toastError(e,'Не удалось сохранить трек')}}
export default MusicPage;
