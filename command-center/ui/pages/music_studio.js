import { api } from '../api.js';
import { h, badge, toastOk, toastError } from '../components.js';
import { errorBanner } from './_shared.js';

let state={
  prompt:'dark aggressive phonk, distorted cowbell melody, punchy 808 bass, Memphis-inspired drums, instrumental',
  lyrics:'[inst]',duration:90,bpm:130,model:'acestep-v15-turbo',presetId:'phonk',
  taskId:'',task:null,health:null,error:null,
  generating:false,serviceBusy:'',
};
let pollTimer=null;

// What the owner reads instead of a transport error: one word per real service state.
const STATUS={
  READY:{label:'Готов',tone:'ok'},
  NOT_INSTALLED:{label:'Не установлен',tone:'err'},
  NOT_RUNNING:{label:'Не запущен',tone:'warn'},
  STARTING:{label:'Запускается…',tone:'info'},
  NOT_READY:{label:'Запущен, не готов',tone:'warn'},
  NOT_CONFIGURED:{label:'Не настроен',tone:'warn'},
};
const WAITING=new Set(['STARTING','NOT_READY']);
const HEALTH_TIMEOUT_MS=15000;

function healthWithTimeout(){
  let timer;
  const timeout=new Promise((_,reject)=>{timer=setTimeout(()=>reject(Object.assign(
    new Error('Проверка ACE-Step не ответила за '+HEALTH_TIMEOUT_MS/1000+' с'),
    {hint:'Сам Bossman работает. Нажмите «Повторить»; если не проходит — перезапустите ACE-Step.'})),HEALTH_TIMEOUT_MS)});
  return Promise.race([api.raw('/api/music/health'),timeout]).finally(()=>clearTimeout(timer));
}

const MusicPage={
  id:'music-studio',title:'Music Studio',icon:'activity',nav:'primary',section:'studio',
  async render(ctx){
    clearTimeout(pollTimer);
    try{state.health=await healthWithTimeout();state.error=null}catch(e){state.error=e;state.health=null}
    const presets=await api.raw('/api/music/presets').catch(()=>({items:[]}));
    const ready=state.health?.status==='READY';
    const unavailable=state.health?.reason||'ACE-Step 1.5 не настроен или не отвечает';
    // While the service starts, look again every few seconds — but only while this page is open.
    if(WAITING.has(state.health?.status)){
      pollTimer=setTimeout(()=>{if(String(location.hash).includes('music-studio'))ctx.refresh()},3000);
    }
    // Choosing a style is local form editing, not a generator health check.
    // Re-rendering via ctx.refresh() waits for ACE-Step and leaves the old
    // fields/buttons visible when that optional service is slow or offline.
    const presetButtons=[];
    const syncPresetSelection=()=>{
      for(const {button,preset} of presetButtons){
        const selected=state.presetId===preset.id;
        button.setAttribute('aria-pressed',selected?'true':'false');
        button.title=selected?'Выбранный стиль':'Выбрать стиль '+preset.label;
        button.classList.toggle('btn-primary',selected);
      }
    };
    const clearPreset=()=>{state.presetId='';syncPresetSelection()};
    const promptInput=h('textarea.input',{value:state.prompt,
      onInput:e=>{state.prompt=e.target.value;clearPreset()},rows:5});
    const bpmInput=h('input.input',{type:'number',min:40,max:240,value:state.bpm,
      onInput:e=>{state.bpm=Number(e.target.value);clearPreset()}});
    const presetControls=(presets.items||[]).map(preset=>{
      const button=h('button.btn.btn-sm',{type:'button',onClick:()=>{
        state.prompt=preset.prompt;state.bpm=preset.bpm;state.presetId=preset.id;
        promptInput.value=state.prompt;bpmInput.value=state.bpm;
        syncPresetSelection();
      }},preset.label);
      presetButtons.push({button,preset});
      return button;
    });
    syncPresetSelection();
    return h('div.stack.lg',
      h('div.page-head',
        h('div',h('h1','Music Studio'),h('div.dim','Локальная генерация музыки · ACE-Step 1.5')),
        statusBadge(state.health?.status)),
      state.error?errorBanner(state.error,ctx):null,
      servicePanel(ctx),
      h('section.panel',
        h('div.panel-head',h('h2','Стиль')),
        h('div.panel-body',
          h('div.row.wrap',...presetControls),
          field('Описание',promptInput),
          h('div.row',
            field('BPM',bpmInput),
            field('Длина, сек',h('input.input',{type:'number',min:10,max:600,value:state.duration,onInput:e=>state.duration=Number(e.target.value)}))),
          field('Lyrics / instrumental',h('textarea.input',{value:state.lyrics,onInput:e=>state.lyrics=e.target.value,rows:4})),
          h('button.btn.btn-primary',{
            type:'button',disabled:!ready||state.generating,
            title:ready?'Сгенерировать трек локально через ACE-Step 1.5':unavailable,
            'data-reason':ready?'':unavailable,
            onClick:e=>{e.currentTarget.disabled=true;generate(ctx)},
          },state.generating?'Ставлю в очередь…':'Сгенерировать трек'),
          !ready?h('div.small.dim','Генерация пока недоступна: '+unavailable):null)),
      state.taskId?taskPanel(ctx):null);
  },
  onEvent(ev){return String(ev.kind||'').startsWith('music.')},
};

function statusBadge(status){
  const info=STATUS[status]||{label:status||'Неизвестно',tone:''};
  return badge(info.label,info.tone);
}

function servicePanel(ctx){
  const health=state.health||{};
  const actions=[];
  if(health.can_start){
    actions.push(h('button.btn.btn-primary',{type:'button',
      title:'Запустить локальный сервис ACE-Step на этом ПК',
      onClick:e=>serviceAction(ctx,'start',e.currentTarget)},'Запустить ACE-Step'));
  }
  if(health.owned&&health.running){
    actions.push(h('button.btn',{type:'button',
      title:'Остановить сервис, который запустил Bossman',
      onClick:e=>serviceAction(ctx,'stop',e.currentTarget)},'Остановить ACE-Step'));
  }
  actions.push(h('button.btn.btn-sm',{type:'button',onClick:()=>ctx.refresh()},'Проверить снова'));
  const log=(health.log_tail||[]);
  return h('section.panel',{dataset:{musicService:health.status||'UNKNOWN'}},
    h('div.panel-head',h('h2','Сервис генерации'),h('div.spacer'),statusBadge(health.status)),
    h('div.panel-body.stack',
      h('div',health.reason||(state.error?'Состояние ACE-Step неизвестно: проверка не выполнена.':'')),
      health.remedy?h('div.small.dim',health.remedy):null,
      health.action==='install'&&health.install_command?installBlock(health.install_command):null,
      h('div.row.wrap',...actions),
      health.status==='READY'&&health.loaded_model?h('div.xsmall.dim','Модель: '+health.loaded_model+(health.loaded_lm_model?' · LM: '+health.loaded_lm_model:'')):null,
      log.length?h('details',h('summary.small','Журнал сервиса'),h('pre.mono.xsmall',{style:{whiteSpace:'pre-wrap',maxHeight:'14em',overflow:'auto'}},log.join('\n'))):null));
}

function installBlock(command){
  return h('div.stack.xs',
    h('div.small','Команда установки (один раз, из папки с Bossman):'),
    h('div.row',
      h('code.mono.small',command),
      h('button.btn.btn-sm',{type:'button',onClick:async()=>{
        try{await navigator.clipboard.writeText(command);toastOk('Команда скопирована')}
        catch{toastError({message:'Не удалось скопировать — выделите команду вручную'})}
      }},'Копировать')));
}

// A click disables its own button at once, so a double click cannot start the service twice.
async function serviceAction(ctx,kind,button){
  if(state.serviceBusy)return;
  state.serviceBusy=kind;
  if(button){button.disabled=true;button.textContent=kind==='start'?'Запускаю…':'Останавливаю…'}
  try{
    const out=await api.raw('/api/music/service/'+kind,{method:'POST'});
    if(out.ok===false)toastError({message:out.message||'ACE-Step не запустился',hint:(out.log_tail||[]).slice(-2).join(' · ')},'ACE-Step');
    else toastOk(out.message||(kind==='start'?'ACE-Step запускается':'ACE-Step остановлен'));
  }catch(e){toastError(e,kind==='start'?'Не удалось запустить ACE-Step':'Не удалось остановить ACE-Step')}
  finally{state.serviceBusy='';ctx.refresh()}
}

function field(label,node){return h('label.stack.xs',h('div.small.dim',label),node)}
function taskPanel(ctx){return h('section.panel',h('div.panel-head',h('h2','Текущий трек')),h('div.panel-body',h('div.small','Task: '+state.taskId),h('div.small','Статус: '+(state.task?.status||'queued')),h('div.row',h('button.btn.btn-sm',{type:'button',onClick:()=>refreshTask(ctx)},'Обновить'),state.task?.status==='completed'?h('button.btn.btn-primary',{type:'button',onClick:()=>save(ctx)},'Сохранить локально'):null)))}
// One click = one provider task: the old page allowed a double click to queue two (UX-010).
async function generate(ctx){if(state.generating)return;state.generating=true;try{const out=await api.raw('/api/music/generate',{method:'POST',body:{prompt:state.prompt,lyrics:state.lyrics,duration:state.duration,bpm:state.bpm,model:state.model,thinking:true}});state.taskId=out.task_id;state.task=out;toastOk('Музыка поставлена в очередь')}catch(e){toastError(e,'Генерация музыки не запущена')}finally{state.generating=false;ctx.refresh()}}
async function refreshTask(ctx){try{state.task=await api.raw('/api/music/tasks/'+encodeURIComponent(state.taskId));ctx.refresh()}catch(e){toastError(e,'Не удалось получить статус')}}
async function save(ctx){try{const out=await api.raw('/api/music/tasks/'+encodeURIComponent(state.taskId)+'/save',{method:'POST'});toastOk('Трек сохранён: '+out.path);state.task=out;ctx.refresh()}catch(e){toastError(e,'Не удалось сохранить трек')}}
export default MusicPage;
