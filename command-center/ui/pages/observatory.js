/* Six read-only projections over canonical records, with no model/brain fiction. */
import {api} from '../api.js';
import {h,toastError} from '../components.js';
import {setTraceEnabled,setTraceRoutes,traceSnapshot,clearTrace} from '../api_trace.js';
const VIEWS={brain:'Brain View',agents:'Live Agent Graph',memory:'Memory Map',learning:'Learning View',xray:'UI → API X-Ray',analysis:'Self-Analysis'};
const text=v=>v===null||v===undefined?'UNKNOWN':String(v);
const tag=v=>h('span.obs-tag',text(v));
const note=t=>h('p.small.dim',t);
const panel=(title,...children)=>h('section.panel.obs-panel',h('h3',title),...children);
const button=(label,fn)=>h('button.btn.btn-sm',{type:'button',onClick:fn},label);
const empty=()=>note('Нет записей в выбранной области. Это не успешный тест и не подтверждение отсутствия проблем.');
function table(headers,rows){
  if(!rows.length)return empty();
  return h('div.obs-table',h('table',h('thead',h('tr',...headers.map(x=>h('th',x)))),h('tbody',...rows.map(row=>h('tr',...row.map(x=>h('td',typeof x==='object'&&x!==null&&x.nodeType?x:text(x))))))));
}
function styles(){
  if(document.getElementById('observatory-style'))return;
  document.head.append(h('link',{id:'observatory-style',rel:'stylesheet',href:'/pages/observatory.css'}));
}
export function graphData(data,mode){
  const nodes=new Map(),edges=[];
  const add=(id,label,level,status)=>{if(!nodes.has(id))nodes.set(id,{id,label,level,status});};
  const edge=(from,to)=>{if(nodes.has(from)&&nodes.has(to))edges.push({from,to});};
  if(mode==='memory'){
    for(const f of data.memory.facts){add('f'+f.id,'Fact #'+f.id,1,f.invalid_at||f.expired_at?'HISTORICAL':'RECORDED');if(f.source_run_id!=null)add('r'+f.source_run_id,'Run #'+f.source_run_id,0,'SOURCE_ID');}
    for(const f of data.memory.facts){if(f.source_run_id!=null)edge('r'+f.source_run_id,'f'+f.id);if(f.superseded_by!=null)edge('f'+f.id,'f'+f.superseded_by);}
  }else{
    for(const t of data.tasks){if(t.agent_id!=null)add('a'+t.agent_id,'Agent #'+t.agent_id,0,'ASSIGNED');add('t'+t.id,'Task #'+t.id,1,t.status);if(t.agent_id!=null)edge('a'+t.agent_id,'t'+t.id);}
    for(const r of data.brain.runs){add('t'+r.task_id,'Task #'+r.task_id,1,'SELECTED');add('r'+r.id,'Run #'+r.id,2,r.status);edge('t'+r.task_id,'r'+r.id);if(r.model_id!=null){add('m'+r.model_id,'Model #'+r.model_id,3,r.model_kind);edge('r'+r.id,'m'+r.model_id);}}
    for(const c of data.brain.tool_calls){add('c'+c.id,c.tool+' #'+c.id,3,c.status);edge('r'+c.run_id,'c'+c.id);}
  }
  const visible=[...nodes.values()].slice(0,100), ids=new Set(visible.map(n=>n.id));
  return {nodes:visible,edges:edges.filter(e=>ids.has(e.from)&&ids.has(e.to)),total:nodes.size};
}
function graph(data,mode,ctx,params){
  const p=graphData(data,mode);if(!p.nodes.length)return empty();
  const counts={};let rows=0;const pos=new Map();
  p.nodes.forEach(n=>{const row=counts[n.level]||0;counts[n.level]=row+1;rows=Math.max(rows,row+1);pos.set(n.id,{x:20+n.level*250,y:25+row*66});});
  const ns='http://www.w3.org/2000/svg';
  const svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox',`0 0 1030 ${Math.max(170,rows*66+40)}`);svg.setAttribute('role','img');svg.setAttribute('aria-label',mode==='memory'?'Recorded memory lineage':'Recorded agent task run graph');
  const make=(type,attrs={})=>{const n=document.createElementNS(ns,type);for(const [k,v]of Object.entries(attrs))n.setAttribute(k,String(v));return n;};
  for(const e of p.edges){const a=pos.get(e.from),b=pos.get(e.to);svg.append(make('path',{d:`M${a.x+205},${a.y+23} C${a.x+238},${a.y+23} ${b.x-28},${b.y+23} ${b.x},${b.y+23}`,class:'obs-edge'}));}
  for(const n of p.nodes){const point=pos.get(n.id);const g=make('g',{'data-node-id':n.id});g.append(make('rect',{x:point.x,y:point.y,width:205,height:48,rx:8,class:'obs-node'}));
    const label=make('text',{x:point.x+9,y:point.y+18,class:'obs-label'});label.textContent=n.label.slice(0,28);g.append(label);
    const status=make('text',{x:point.x+9,y:point.y+37,class:'obs-status'});status.textContent=n.status;g.append(status);
    if(n.id.startsWith('t')){g.setAttribute('tabindex','0');g.setAttribute('role','button');g.setAttribute('aria-label','Открыть задачу '+n.id.slice(1));const open=()=>ctx.navigate('observatory',{...params,task:n.id.slice(1),view:'brain'});g.addEventListener('click',open);g.addEventListener('keydown',e=>{if(e.key==='Enter')open();});}
    svg.append(g);
  }
  return h('div.obs-graph',note(`Узлы ${p.nodes.length}/${p.total}. Линии отражают только сохранённые ID-связи, не сходство мыслей.`),svg);
}
function brain(d){const b=d.brain;return h('div.stack',
  panel('Задача → запуск → модель → инструмент',note('Показаны события и метаданные выполнения, не скрытые рассуждения модели. Запись completed не доказывает независимую проверку результата.'),
    table(['Run','Состояние','Модель','Вход / выход, токены','Стоимость в журнале, $'],b.runs.map(r=>[r.id,tag(r.status),r.model_id==null?'UNKNOWN':`${r.model_kind} #${r.model_id}`,`${text(r.tokens_in)} / ${text(r.tokens_out)}`,r.cost_usd]))),
  panel('Инструменты и права',table(['Вызов','Run / шаг','Инструмент','Режим','Состояние','мс'],b.tool_calls.map(c=>[c.id,`${c.run_id} / ${c.step}`,c.tool,tag(c.effect),tag(c.status),c.duration_ms]))),
  panel('Подтверждения',table(['ID','Run','Статус','Создано'],b.approvals.map(a=>[a.id,a.run_id,tag(a.status),a.created_at]))),
  panel('Записанные проверки',note('Это записи verification.result, а не повторная независимая проверка этой панелью.'),table(['ID','Run','Записанный статус','Время'],b.recorded_verifications.map(v=>[v.id,v.run_id,tag(v.status),v.ts]))));}
function memory(d,ctx,p){return panel('Память: происхождение и замены',note(`Область: ${d.memory.scope}. Только метаданные; тексты памяти и личные файлы не читаются этим представлением.`),graph(d,'memory',ctx,p),table(['Fact','Источник','Run','Заменён на','Уверенность в записи'],d.memory.facts.map(f=>[f.id,f.source_kind,f.source_run_id,f.superseded_outside_window?'OUTSIDE_WINDOW':f.superseded_by,f.confidence])));}
function learning(d){return panel('Сравнение версий навыков',note('Последние 50 записей оценок экземпляра. Рекомендация promote не означает применение. Здесь нет кнопки обучения весов, установки обновления или изменения разрешений.'),
 table(['Оценка / навык','Версии: база → кандидат','Состояние / вердикт','Применено','Запуски','Успешность: база → кандидат','мс: база → кандидат'],d.learning.evaluations.map(e=>[`${e.id} / ${e.skill_id}`,`${e.baseline_version_id} → ${e.candidate_version_id}`,`${e.status} / ${e.verdict}`,e.applied===true?'YES':'NO',`${text(e.baseline_runs)} → ${text(e.candidate_runs)}`,`${text(e.baseline_success_rate)} → ${text(e.candidate_success_rate)}`,`${text(e.baseline_avg_duration_ms)} → ${text(e.candidate_avg_duration_ms)}`])));}
function xray(ctx){
  const area=h('div'),info=note('');
  const paint=()=>{const s=traceSnapshot();info.textContent=`${s.enabled?'ON':'OFF'} · ${s.rows.length}/${s.limit} записей только этой вкладки. Raw fetch, WebSocket, серверные и DB spans не перехватываются.`;
    area.replaceChildren(table(['UI-метка','Метод','Контракт маршрута','HTTP','мс','Транспорт'],s.rows.map(r=>[r.ui_action,r.method,r.path,r.status,r.duration_ms,r.outcome])));};
  paint();
  return panel('UI → API X-Ray',note('Запись выключена по умолчанию. URL-параметры, тела, заголовки, ответы и секреты не сохраняются. При logout данные стираются. HTTP_OK означает транспорт, не успешную бизнес-операцию.'),
    h('div.row',button('Включить X-Ray',async()=>{try{const routes=await api.raw('/api/observatory/routes');setTraceRoutes(routes.routes);setTraceEnabled(true);paint();}catch(e){toastError(e);}}),button('Выключить и очистить',()=>{setTraceEnabled(false);paint();}),button('Обновить X-Ray',paint),button('Очистить',()=>{clearTrace();paint();})),info,area);
}
function analysis(d){const names={FAILED_TASKS:'Задачи со статусом failed в последних 50',WAITING_APPROVAL:'Ждут подтверждения в последних 50',COMPLETED_WITHOUT_VERIFICATION_RECORD_IN_WINDOW:'Завершённые запуски выбранной задачи без записи проверки в окне',TOOL_ERROR_IN_SELECTED_WINDOW:'Ошибки инструментов выбранной задачи в окне'};
 return panel('Самоанализ по наблюдаемым данным',note('Детерминированная диагностика, не оценка IQ и не самостоятельное изменение ядра. Усечённая выборка не доказывает отсутствие проблем.'),
 h('div.obs-cards',...d.analysis.observations.map(o=>h('article.obs-card',h('strong.obs-number',text(o.count)),h('span',names[o.code]||o.code)))),note('Восстановление: откройте задачу и журнал, проверьте её права и состояние. Не повторяйте внешний эффект с неизвестным исходом. Автоисправления отключены.'));
}
export default {
 id:'observatory',title:'Brain & Observatory',icon:'activity',nav:'primary',section:'brains',
 async render(ctx,params={}){
  styles();const view=VIEWS[params.view]?params.view:'brain';const query=new URLSearchParams();
  if(params.task)query.set('task_id',params.task);if(params.project)query.set('project_id',params.project);
  const root=h('div.obs-root.stack.lg',h('div',h('p.small.dim','BOSSMAN / OBSERVATORY'),h('h1','Система под наблюдением'),note('Шесть представлений одного ядра. Никаких фиктивных нейронов, демо-телеметрии или нового агента.')));
  root.append(h('nav.obs-tabs',...Object.entries(VIEWS).map(([id,label])=>h('button.btn',{type:'button','aria-pressed':id===view,onClick:()=>ctx.navigate('observatory',{...params,view:id})},label))));
  try{
   const d=await api.raw('/api/observatory/snapshot'+(query.size?'?'+query:''),{traceOrigin:'Observatory.snapshot'});
   if(d.schema_version!==1||!Array.isArray(d.tasks)||!d.brain)throw Error('Неподдерживаемый ответ Observatory.');
   const select=h('select.input',{'aria-label':'Задача Observatory',onChange:e=>ctx.navigate('observatory',{...params,task:e.target.value,view})},h('option',{value:''},'Выберите задачу'),...d.tasks.map(t=>h('option',{value:String(t.id)},`#${t.id} · ${t.status}`)));
   if(params.task&&!d.tasks.some(t=>String(t.id)===params.task))select.append(h('option',{value:params.task},'#'+params.task+' · вне окна задач'));
   select.value=params.task||'';
   const project=h('input.input',{'aria-label':'Проект Memory Map',value:params.project||'',placeholder:'ID проекта для карты памяти',maxLength:96});
   root.append(h('div.obs-controls',select,project,button('Открыть область памяти',()=>ctx.navigate('observatory',{...params,project:project.value.trim(),view:'memory'})),button('Обновить данные',()=>ctx.refresh())),
    note(`Источник: ${d.source} · снимок ${d.observed_at} · WebSocket: ${ctx.bus?.state||'UNKNOWN'}. Обновления только при событиях открытой страницы или вручную.`));
   if(Object.values(d.truncated).some(Boolean)||d.memory.truncated||d.learning.truncated)root.append(h('p.obs-warning','Выборка ограничена. Показанные счётчики и отсутствие записей относятся только к этому окну.'));
   root.append(view==='brain'?brain(d):view==='agents'?panel('Агенты → задачи → запуски → инструменты',graph(d,'agents',ctx,params)):view==='memory'?memory(d,ctx,params):view==='learning'?learning(d):view==='xray'?xray(ctx):analysis(d));
  }catch(e){root.append(panel('Данные недоступны',h('p',{role:'alert'},e.message),button('Повторить чтение',()=>ctx.refresh())));}
  return root;
 },
 onEvent(ev){return document.visibilityState==='visible'&&/^(task\.|run\.|tool\.|approval\.|fact\.|skill\.|verification\.|ws\.)/.test(String(ev.kind||''));}
};
