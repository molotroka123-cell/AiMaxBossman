/* One Studio surface inside the existing Images page. No provider polling loop. */
import {api} from '../api.js';
import {h,toast,toastError,toastOk} from '../components.js';
let draft={model:'mock:image',prompt:'',settings:{},media:[],count:1,collection_id:null};
let scope='all',query='',offset=0,selected=new Set(),proof=null;
const labels={width:'Ширина',height:'Высота',steps:'Шаги',seed:'Seed',aspect_ratio:'Формат',duration:'Длительность, с',length:'Длительность ролика',resolution:'Разрешение',generate_audio:'Создать звук'};
/* Подписи вариантов длительности (sd.cpp Wan): 10/15/30 с — цепочка 5-секундных сегментов. */
const optionLabels={length:{custom:'Вручную (кадры × fps)',test_1s:'1 с — TestRun (быстро, 640×352)','5s':'5 с','10s':'10 с (2 сегмента)','15s':'15 с (3 сегмента)','30s':'30 с (6 сегментов)'}};
const call=(path,body,method='POST')=>api.raw('/api/studio'+path,{method,...(body===undefined?{}:{body})});
/* Проверка того, что владелец ввёл сам, — предупреждение и возврат, как во
   всём остальном продукте (см. createJob в images.js). Исключение здесь
   означало бы console.error на пустую форму: обход интерфейса считает это
   вердиктом error, а гейт — REVIEW_REQUIRED. toastError остаётся для
   настоящих отказов. */
const action=(ctx,fn)=>async()=>{try{await fn();ctx.refresh();}catch(e){toastError(e);}};
const choose=(node,value)=>{node.value=String(value??'');return node;};
function button(label,fn,cls='btn btn-sm'){return h('button',{type:'button',class:cls,onClick:fn},label);}

export async function studioPanel(ctx){
  styles();
  const params=new URLSearchParams({limit:'60',offset:String(offset)});
  if(['image','video','audio'].includes(scope))params.set('surface',scope);
  if(scope==='favorite')params.set('favorite','true');
  if(scope==='trash')params.set('deleted','true');
  if(query)params.set('q',query);
  const [catalog,gallery,queue,budget,policy,collections]=await Promise.all([
    api.raw('/api/studio/models'),api.raw('/api/studio/runs?'+params),api.raw('/api/studio/jobs'),api.raw('/api/studio/budget'),api.raw('/api/studio/policy'),api.raw('/api/images/collections')]);
  const model=catalog.items.find(m=>m.id===draft.model)||catalog.items[0];
  const submit=action(ctx,async()=>{
    if(!draft.prompt.trim()){toast('Опишите желаемый результат.',{type:'warn'});return;}
    if(model.provider==='openrouter' && draft.media.length){
      if(!window.confirm('Отправить выбранные референсы в OpenRouter? Это передача ваших файлов внешнему провайдеру.'))return;
      await call('/egress/confirm',{provider:'openrouter',media:draft.media});
    }
    await call('/jobs',{...draft,prompt:draft.prompt.trim()});toastOk('Задача добавлена в общую очередь');
  });
  const settings=Object.entries(model.settings).map(([name,schema])=>{
    const current=draft.settings[name]??schema.default;
    let input;
    if(schema.type==='enum') input=choose(h('select.input',{'aria-label':labels[name]||name,onChange:e=>{draft.settings[name]=schema.values[e.target.selectedIndex];}},schema.values.map(v=>h('option',{value:String(v)},optionLabels[name]?.[v]??String(v)))),current);
    else if(schema.type==='boolean')input=h('input',{type:'checkbox',checked:current,onChange:e=>{draft.settings[name]=e.target.checked;}});
    else input=h('input.input',{type:'number',min:schema.min,max:schema.max,step:schema.multiple_of||1,value:current??'',placeholder:schema.nullable?'Случайный':'','aria-label':labels[name]||name,onInput:e=>{draft.settings[name]=e.target.value===''?null:Number(e.target.value);}});
    return h('label.studio-field',h('span.xsmall.dim',labels[name]||name),input);
  });
  const prompt=h('textarea.input.studio-prompt',{'aria-label':'Промпт Studio',placeholder:'Опишите кадр, короткий ролик или идею…',value:draft.prompt,onInput:e=>{draft.prompt=e.target.value;},onKeydown:e=>{if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){e.preventDefault();submit();}}});
  const cards=gallery.items.map(run=>{
    const p=run.provenance;const checked=selected.has(run.id);
    const preview=run.surface==='video'?h('video',{src:run.file_url,controls:true,preload:'metadata'}):run.surface==='audio'?h('audio',{src:run.file_url,controls:true,preload:'metadata'}):h('img',{src:run.file_url,alt:p.plane.prompt,loading:'lazy'});
    return h('article.studio-card',
      scope==='trash'?h('div.studio-media-placeholder','В корзине'):preview,
      h('div.studio-card-body',h('label.xsmall',h('input',{type:'checkbox',checked,onChange:e=>{e.target.checked?selected.add(run.id):selected.delete(run.id);}}),' Выбрать'),
      h('strong',p.plane.prompt||'Результат'),h('div.xsmall.dim',`${run.surface} · ${run.model}${p.mock?' · ДЕМО':''}`),
      h('div.studio-actions',button('Настройки и происхождение',()=>{proof=run;ctx.refresh();}),
      button(run.favorite?'Убрать звезду':'В избранное',action(ctx,()=>call('/runs/'+run.id,{favorite:!run.favorite},'PATCH'))),
      button(scope==='trash'?'Восстановить':'В корзину',action(ctx,()=>call('/runs/'+run.id+(scope==='trash'?'/restore':''),undefined,scope==='trash'?'POST':'DELETE')))),
      h('div.studio-actions',h('a.btn.btn-sm',{href:run.file_url,download:''},'Скачать'),
      ...Object.keys(model.roles).map(role=>button('Референс: '+role,()=>{draft.media.push({run_id:run.id,role});toastOk('Референс выбран');ctx.refresh();})),
      button('Рефрейм',()=>{draft={model:'local:reframe',prompt:p.plane.prompt+' / рефрейм',settings:{width:1024,height:1024,mode:'pad'},media:[{run_id:run.id,role:'reference'}],count:1,collection_id:null};ctx.refresh();}),
      button('В память: референс',action(ctx,async()=>{const title=window.prompt('Название референса',p.plane.prompt.slice(0,120)||'Референс');if(!title)return;await call('/runs/'+run.id+'/memory',{title});toastOk('Ссылка и хэш сохранены в настроенной папке заметок');})),
      button('В Web Designer',action(ctx,async()=>{const id=window.prompt('ID проекта Web Designer');if(!id)return;const project=await api.raw('/api/web-designer/projects/'+encodeURIComponent(id));const path=window.prompt('CSS-путь существующего изображения','html > body > img');if(!path)return;await call('/runs/'+run.id+'/web',{project_id:Number(id),base_version:project.meta.version,path});toastOk('Изображение вставлено');})),
      button('В Video Studio',action(ctx,async()=>{
        const projects=await api.raw('/api/video-studio/projects');
        const items=Array.isArray(projects)?projects:(projects.projects||projects.items||[]);
        if(!items.length)throw new Error('Сначала создайте проект в Video Studio.');
        const pid=window.prompt('ID проекта: '+items.map(p=>`${p.project_id||p.id} (${p.name})`).join(', '),items[0].project_id||items[0].id);if(!pid)return;
        const project=await api.raw('/api/video-studio/projects/'+encodeURIComponent(pid));
        await call('/runs/'+run.id+'/video',{project_id:pid,expected_revision:project.revision,operation_id:crypto.randomUUID()});toastOk('Медиа добавлено в Video Studio');
      })))));
  });
  const active=queue.items.filter(j=>['queued','running'].includes(j.status));
  return h('section.studio-surface',
    h('div.studio-hero',h('div',h('div.xsmall.dim','BOSSMAN / CREATIVE WORKSPACE'),h('h2','От идеи — к своему результату'),h('p.dim','Изображения и видео. Файлы остаются у вас.')),h('div.studio-budget',`Лимит $${budget.cloud_budget_usd} / зарезервировано $${budget.committed_upper_bound_usd.toFixed(3)}`,h('div.xsmall',budget.enabled?(budget.free_only?'Только бесплатные маршруты':'Облако включено владельцем'):'Облако выключено'))),
    h('div.studio-composer',prompt,h('div.studio-controls',
      choose(h('select.input',{'aria-label':'Модель Studio',onChange:e=>{draft.model=e.target.value;draft.settings={};draft.media=[];ctx.refresh();}},catalog.items.map(m=>h('option',{value:m.id},`${m.label} · ${m.verified?'проверено':'не проверено'}`))),draft.model),
      h('label.studio-field',h('span.xsmall.dim','Количество'),h('input.input',{type:'number',min:1,max:8,value:draft.count,onInput:e=>{draft.count=Number(e.target.value);}})),
      choose(h('select.input',{'aria-label':'Коллекция Studio',onChange:e=>{draft.collection_id=e.target.value?Number(e.target.value):null;}},h('option',{value:''},'Без коллекции'),collections.map(c=>h('option',{value:c.id},c.name))),draft.collection_id),
      button('Создать результат',submit,'btn btn-primary'),button('Раскадровка: 5 кадров',action(ctx,async()=>{if(!draft.prompt.trim()){toast('Опишите идею для раскадровки.',{type:'warn'});return;}const result=await call('/storyboard',{prompt:draft.prompt,model:draft.model,settings:draft.settings});toastOk('Создана коллекция #'+result.collection_id); }))),
      h('div.studio-settings',settings),h('div.xsmall.dim',model.demo?'Демо создаёт тестовый рисунок локально. Это не AI-модель.':`${model.surface} · цена ${model.price.usd===null?'неизвестна':'$'+model.price.usd} · ${model.status}`),
      draft.media.length?h('div.studio-actions',`${draft.media.length} референсов`,button('Очистить референсы',()=>{draft.media=[];ctx.refresh();})):null),
    h('div.studio-actions',...['all','image','video','audio','favorite','trash'].map((s,i)=>button(['Все результаты','Изображения Studio','Видео Studio','Звук Studio','Избранное Studio','Корзина Studio'][i],()=>{scope=s;offset=0;selected.clear();ctx.refresh();},'btn btn-sm'+(scope===s?' btn-primary':''))),
      h('input.input',{type:'search','aria-label':'Поиск Studio',placeholder:'Поиск по промпту…',value:query,onInput:e=>{query=e.target.value;},onKeydown:e=>{if(e.key==='Enter'){offset=0;ctx.refresh();}}}),button('Найти в Studio',()=>{offset=0;ctx.refresh();})),
    h('label.studio-field','Добавить свои референсы (до 15 МиБ)',h('input',{type:'file',accept:'.png,.jpg,.jpeg,.mp4,.wav,.mp3','aria-label':'Импортировать референс',onChange:action(ctx,async()=>{const input=document.querySelector('[aria-label="Импортировать референс"]');const file=input.files[0];if(!file)return;if(file.size>15*1024*1024)throw new Error('Максимальный размер — 15 МиБ');const encoded=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result.split(',')[1]);r.onerror=reject;r.readAsDataURL(file);});await call('/references',{filename:file.name,data_base64:encoded});toastOk('Референс сохранён локально');})})),
    h('div.studio-actions',button('Выбранные в корзину',action(ctx,async()=>{for(const id of selected)await call('/runs/'+id,undefined,'DELETE');selected.clear();})),button('Скачать выбранные ZIP',action(ctx,async()=>{if(!selected.size){toast('Сначала выберите результаты.',{type:'warn'});return;}const res=await call('/runs/package',{ids:[...selected]});window.location.href=res.download_url;}))),
    active.length?h('div.studio-queue',h('h3','В работе'),active.map(j=>h('div.studio-actions',h('span',j.prompt+' · '+j.status),button('Остановить #'+j.id,action(ctx,()=>call('/jobs/'+j.id+'/cancel')))))):null,
    queue.items.filter(j=>j.status==='failed').slice(0,5).map(j=>h('div.studio-error',`${j.prompt}: ${j.studio.reason} — ${j.error}`,button('Повторить #'+j.id,action(ctx,()=>call('/jobs/'+j.id+'/retry'))))),
    cards.length?h('div.studio-grid',cards):h('div.studio-empty','Здесь появятся ваши результаты. Начните с промпта выше.'),
    h('div.studio-actions',`${gallery.total} результатов`,offset>0?button('Предыдущие',()=>{offset=Math.max(0,offset-60);ctx.refresh();}):null,offset+60<gallery.total?button('Следующие',()=>{offset+=60;ctx.refresh();}):null),
    proof?h('section.studio-proof',h('h3','Происхождение результата'),h('pre',JSON.stringify(proof.provenance,null,2)),button('Повторить настройки',action(ctx,async()=>{const r=await call('/runs/'+proof.id+'/reuse');draft={...r.plane,media:r.plane.media.map(({run_id,role})=>({run_id,role}))};proof=null;})),button('Закрыть происхождение',()=>{proof=null;ctx.refresh();})):null,
    policyPanel(ctx,policy),
  );
}
function policyPanel(ctx,p){
  const prices=h('textarea.input',{'aria-label':'Верхняя стоимость моделей',value:JSON.stringify(p.prices,null,2),rows:3});
  const hosts=h('input.input',{'aria-label':'Разрешённые CDN',value:p.download_hosts.join(', ')});
  const enabled=h('input',{type:'checkbox',checked:p.enabled});const free=h('input',{type:'checkbox',checked:p.free_only});
  const daily=h('input.input',{type:'number',min:0,step:0.01,value:p.cloud_budget_usd});const per=h('input.input',{type:'number',min:0,step:0.01,value:p.per_job_usd});
  return h('details.studio-policy',h('summary','Подключение и расходы'),h('p.dim','Ключ OpenRouter берётся из защищённых настроек провайдера или окружения. Здесь ключи не вводятся. Укажите подтверждённую верхнюю стоимость одного результата; пустая цена блокирует вызов. Нулевую ставьте только для подтверждённого бесплатного маршрута.'),
    h('label',enabled,' Включить OpenRouter'),h('label',free,' Только бесплатные'),h('label.studio-field','Дневной лимит, USD',daily),h('label.studio-field','Лимит задачи, USD',per),h('label.studio-field','Верхняя стоимость по ID модели (JSON)',prices),h('label.studio-field','Точные домены CDN, через запятую',hosts),
    button('Сохранить правила облака',action(ctx,async()=>{await call('/policy',{enabled:enabled.checked,free_only:free.checked,cloud_budget_usd:Number(daily.value),per_job_usd:Number(per.value),prices:JSON.parse(prices.value),download_hosts:hosts.value.split(',').map(x=>x.trim()).filter(Boolean)},'PUT');toastOk('Правила сохранены');})),
    button('Отозвать разрешения на референсы',action(ctx,()=>call('/egress/confirmations',undefined,'DELETE'))),
    h('p.xsmall.dim','Higgsfield: официальный MCP ещё не прошёл приёмку на этой машине. REST не настроен. Звук и сторонняя постобработка появятся только после подтверждения провайдера.'));
}
function styles(){if(document.getElementById('studio-v8-style'))return;const s=document.createElement('style');s.id='studio-v8-style';s.textContent=`
.studio-surface{display:grid;gap:20px}.studio-hero{display:flex;justify-content:space-between;gap:24px;padding:26px;border:1px solid var(--border);border-radius:18px;background:linear-gradient(120deg,rgba(78,173,126,.12),transparent)}.studio-hero h2{margin:8px 0;font-size:28px}.studio-budget{font-size:13px;text-align:right}.studio-composer,.studio-proof,.studio-policy{padding:20px;border:1px solid var(--border);border-radius:14px;background:var(--panel)}.studio-prompt{min-height:105px;width:100%;resize:vertical;font-size:16px}.studio-controls,.studio-settings,.studio-actions{display:flex;flex-wrap:wrap;gap:10px;align-items:center}.studio-controls{margin:14px 0}.studio-controls>select{max-width:430px}.studio-field{display:grid;gap:6px;max-width:100%;margin:6px 0}.studio-settings input{width:120px}.studio-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:18px}.studio-card{border:1px solid var(--border);border-radius:14px;overflow:hidden;min-width:0}.studio-card img,.studio-card video{width:100%;height:215px;object-fit:contain;background:#10141c}.studio-card audio{width:100%;margin:30px 0}.studio-card-body{padding:14px;display:grid;gap:12px}.studio-card-body strong{overflow-wrap:anywhere}.studio-empty,.studio-media-placeholder{padding:60px 20px;text-align:center;color:var(--dim)}.studio-proof pre{max-height:360px;overflow:auto;font-size:12px;white-space:pre-wrap}.studio-error{padding:14px;border:1px solid #bb744d;border-radius:10px}.studio-policy summary{cursor:pointer;font-weight:600}.studio-policy label{margin-right:18px}.studio-policy textarea{min-width:300px}@media(max-width:700px){.studio-hero{display:block}.studio-budget{text-align:left;margin-top:16px}.studio-controls>select{max-width:100%}}
`;document.head.append(s);}
