/* VFX affordances for the existing editor/Studio. No second store or renderer. */
import {api} from '../api.js';
import {h, toastError, toastOk} from '../components.js';
const BASE = '/api/video-studio';
const uid = () => crypto.randomUUID();
const btn = (text, action, extra={}) => h('button.btn.btn-sm', {type:'button', onClick:action, ...extra}, text);
const value = (label, input) => h('label.stack.sm', h('span', label), input);
function style(){if(!document.getElementById('observatory-style'))document.head.append(h('link',{id:'observatory-style',rel:'stylesheet',href:'/pages/observatory.css'}));}

export function buildVfxCommand(project, clipIds, params, vertical15=false) {
  if (!Array.isArray(clipIds) || !clipIds.length || clipIds.length>100 || new Set(clipIds).size!==clipIds.length) throw Error('Выберите 1–100 разных визуальных клипов.');
  const seq=project.sequences.find(s=>s.id===project.active_sequence_id);
  if (!seq) throw Error('Активная секвенция не найдена.');
  const visual=new Set(seq.tracks.filter(t=>t.kind!=='audio'&&!t.locked).flatMap(t=>t.clips.filter(c=>!c.audio_only&&(c.title||c.adjustment||c.nested_sequence_id||project.media[c.media_id]?.has_video)).map(c=>c.id)));
  if (clipIds.some(id=>!visual.has(id))) throw Error('Клип исчез, не визуальный или его дорожка заблокирована.');
  const operations=clipIds.map(id=>({type:'effect.apply',clip_id:id,effect:{id:'viral-vfx',type:'viral_vfx',enabled:true,params:{...params}}}));
  if (vertical15) operations.push({type:'sequence.settings',sequence_id:seq.id,patch:{width:1080,height:1920,fps:{num:30,den:1}}},{type:'range.set',sequence_id:seq.id,start:0,end:15000000});
  return {type:'timeline.apply',operations};
}

export async function openVfxPanel(root, ctx) {
  style();
  const pid=new URLSearchParams(location.hash.split('?')[1]||'').get('project_id');
  if (!pid) throw Error('Откройте проект в Video Studio.');
  const ids=[...root.querySelectorAll('.vs-clip.selected')].map(n=>n.dataset.clipId);
  if (!ids.length) throw Error('Сначала выберите визуальный клип на таймлайне.');
  const [project,catalog]=await Promise.all([api.raw(`${BASE}/projects/${encodeURIComponent(pid)}`),api.raw(`${BASE}/vfx-catalog`)]);
  if (!root.isConnected || new URLSearchParams(location.hash.split('?')[1]||'').get('project_id')!==pid) throw Error('Проект изменился — откройте панель снова.');
  const preset=h('select.input',{'aria-label':'VFX preset'}, ...catalog.presets.map(p=>h('option',{value:p.id},p.name)));
  preset.value='phonk_hard';
  const numeric=(label,initial,min,max,step)=>h('input.input',{type:'number','aria-label':label,value:initial,min,max,step});
  const intensity=numeric('VFX intensity',.5,0,1,.05), bpm=numeric('VFX BPM',140,60,200,1), offset=numeric('VFX offset',0,-60,60,.01);
  const vertical=h('input',{type:'checkbox','aria-label':'VFX 15 seconds'});
  const message=h('p',{role:'status'}); let review=null,busy=false,finished=false;
  const dialog=h('dialog.vs-dialog.viral-vfx-dialog');
  const binding=()=>JSON.stringify([preset.value,intensity.value,bpm.value,offset.value,vertical.checked]);
  const current=()=>root.isConnected && new URLSearchParams(location.hash.split('?')[1]||'').get('project_id')===pid;
  const close=()=>{if(!busy){dialog.close();dialog.remove();}};
  const apply=btn('Применить VFX',async()=>{
    if(busy||finished||!review)return;
    if(!current()||review.binding!==binding()){review=null;apply.disabled=true;message.textContent='Вход изменился — проверьте план снова.';return;}
    busy=true;apply.disabled=true;check.disabled=true;
    try {
      const result=await api.raw(`${BASE}/commands`,{method:'POST',body:{...review.payload,dry_run:false},traceOrigin:'VideoStudio.command.apply'});
      finished=true;message.textContent=`Сохранено r${result.revision}. Для 15 с выберите «Диапазон» при экспорте. Undo доступен в редакторе.`;
      toastOk('VFX сохранён в редактируемом проекте');
      ctx.refresh();
    } catch(e){review=null;message.textContent='Не подтверждено: '+e.message+' Не повторяйте вслепую: откройте историю проекта.';}
    finally{busy=false;if(!finished)check.disabled=false;}
  },{disabled:true});
  const check=btn('Проверить VFX-план',async()=>{
    if(busy||finished)return;
    review=null;apply.disabled=true;busy=true;check.disabled=true;
    try {
      if(!current())throw Error('Проект изменился.');
      if([intensity,bpm,offset].some(el=>el.value.trim()===''||!el.checkValidity()))throw Error('Проверьте интенсивность, BPM и сдвиг.');
      const bound=binding();
      const command=buildVfxCommand(project,ids,{preset:preset.value,intensity:Number(intensity.value),bpm:Number(bpm.value),offset:Number(offset.value)},vertical.checked);
      const payload={project_id:pid,expected_revision:project.revision,operation_id:uid(),command,dry_run:true};
      const result=await api.raw(`${BASE}/commands`,{method:'POST',body:payload,traceOrigin:'VideoStudio.command.dry_run'});
      if(bound!==binding()||!current())throw Error('Вход изменился во время проверки.');
      if(result.dry_run!==true)throw Error('Нет подтверждения dry-run.');
      review={payload,binding:bound};apply.disabled=false;
      message.textContent=`План проверен без записи. Клипов: ${ids.length}; база r${project.revision}. ${vertical.checked?'9:16, 30 fps, диапазон 0–15 с. ':''}Пресет заменит только прежний Viral VFX; остальные эффекты и звук сохранятся.`;
    } catch(e){message.textContent=e.message;}
    finally{busy=false;check.disabled=false;}
  });
  for(const el of [preset,intensity,bpm,offset,vertical])el.addEventListener('input',()=>{review=null;apply.disabled=true;});
  dialog.append(h('h2','Viral VFX · Phonk'),h('p','24 эффекта + 6 комбинаций. BPM и первый удар задаются вручную, аудио не анализируется. Начните с низкой интенсивности; резкое движение может быть дискомфортным.'),
    value('Пресет',preset),value('Интенсивность',intensity),value('BPM',bpm),value('Первый удар (секунды)',offset),
    h('label',vertical,' Настроить 9:16 / 30 fps / первые 15 с. Требуется не менее 15 с материала; исходники не обрезаются.'),
    message,h('div.row',check,apply,btn('Закрыть',close)));
  dialog.addEventListener('cancel',e=>{e.preventDefault();close();});
  document.body.append(dialog);dialog.showModal();return dialog;
}

export function shotRecipePanel(setPrompt) {
  style();
  const content=h('div');let loading=false;
  return h('details.viral-shot-recipes',h('summary','15 секунд · Phonk / Higgsfield: сцены'),
    h('p','Три монтажных фрагмента по 5 с. Это черновики промптов, не вызов Higgsfield и не гарантированная длительность модели. Музыка — ваш разрешённый файл.'),
    btn('Показать 3 сцены',async()=>{
      if(loading)return;loading=true;
      try {
        const data=await api.raw(`${BASE}/vfx-catalog`,{traceOrigin:'Studio.shotRecipes'});
        content.replaceChildren(...data.shots.map((s,i)=>h('article.panel',h('strong',`Сцена ${i+1} · ${s.start_s}–${s.start_s+s.duration_s} с`),h('p',s.prompt),btn('Вставить только промпт',()=>setPrompt(s.prompt)))));
      }catch(e){toastError(e);}finally{loading=false;}
    }),content,h('p.small.dim','Создание результата остаётся отдельным действием Studio с обычными разрешениями и бюджетом. После получения клипа используйте «В Video Studio».'));
}
