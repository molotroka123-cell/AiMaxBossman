import { api } from './api.js';
import { h, toastError, toastOk } from './components.js';
const state = { text: '', files: [], requestId: '', lastProject: '', requestKey:'', uploaded:new Set() };
const newId = () => crypto.randomUUID();
export async function routeVideoRequest(text, files, ctx) {
  const key=JSON.stringify([text,files.map(f=>[f.name,f.size,f.lastModified])]);
  if (!state.requestId || state.requestKey!==key) {state.requestId=newId();state.requestKey=key;state.uploaded.clear();}
  const payload={text,operation_id:state.requestId};
  if(state.lastProject && /^(открой|open)\b/i.test(text.trim())) payload.project_id=state.lastProject;
  const response = await api.raw('/api/video-studio/chat', {method:'POST', body:payload});
  if (!response.handled) { state.requestId=''; return false; }
  state.lastProject = response.project_id;
  let project = await api.raw(`/api/video-studio/projects/${response.project_id}`);
  for (let i=0;i<files.length;i++) {
    const file=files[i];
    if(state.uploaded.has(i)) continue;
    const params = new URLSearchParams({project_id:response.project_id,filename:file.name,
      expected_revision:String(project.revision),operation_id:`${state.requestId}-file-${i}`});
    const uploaded=await api.raw(`/api/video-studio/media?${params}`,{method:'POST',body:file});
    project=uploaded.project;state.uploaded.add(i);
  }
  await api.raw(`/api/video-studio/chat/${response.task_id}/run`,{method:'POST'});
  state.text='';state.files=[];state.requestId='';
  window.dispatchEvent(new CustomEvent('bcc:video-open',{detail:response}));
  ctx.navigate('video-studio',{project_id:response.project_id});
  toastOk('Проект открыт; задача сохранена');
  return true;
}
export function attachmentInput() {
  const input=h('input',{type:'file',multiple:true,accept:'video/*,audio/*,image/*,.srt,.vtt',
      'aria-label':'Прикрепить медиа'});
  const names=h('small',state.files.map(file=>file.name).join(' · '));
  input.addEventListener('change',()=>{state.files=Array.from(input.files||[]);state.requestId='';names.textContent=state.files.map(file=>file.name).join(' · ');});
  const remove=h('button.bx-btn',{type:'button',onClick:()=>{state.files=[];state.requestId='';input.value='';names.textContent='';}},'Убрать вложения');
  return h('div',input,names,remove);
}
export const attachedFiles=()=>state.files;
export const ChatPage={id:'bossman-chat',title:'Bossman Chat',icon:'terminal',section:'main',nav:'primary',
  async render(ctx) {
    let records=[];
    try {records=(await api.raw('/api/video-studio/chat')).messages||[];}catch(e){toastError(e);}
    const taskResults=await Promise.allSettled(records.slice(0,20).map(row=>api.task(row.task_id)));
    const resultById=new Map(records.slice(0,20).map((row,i)=>[row.task_id,taskResults[i].status==='fulfilled'?taskResults[i].value:null]));
    const input=h('textarea',{rows:4,placeholder:'Склей эти два видео',value:state.text,
      'aria-label':'Задание для Video Studio'});
    input.addEventListener('input',()=>{state.text=input.value;state.requestId='';});
    const files=attachmentInput();
    const status=h('p',{role:'status'},'Файлы остаются локально. Теоретические вопросы не создают проект.');
    const send=h('button.bx-btn.bx-btn-primary',{type:'button'},'Отправить');
    send.addEventListener('click',async()=>{
      send.disabled=true;
      try {
        if(!await routeVideoRequest(input.value.trim(),state.files,ctx))
          status.textContent='Это вопрос, а не команда монтажа. Для общего агента используйте поле на главной.';
      } catch(e){toastError(e);status.textContent=e.message;}
      finally{send.disabled=false;}
    });
    return h('section.bx-panel',h('div.bx-panel-body',h('h2','Bossman Chat'),input,files,send,status,
      ...records.map(row=>h('article.bx-panel',h('p',row.text),h('p',`Задача #${row.task_id} · ${resultById.get(row.task_id)?.task?.status||'сохранена'}`),
        resultById.get(row.task_id)?.result?h('pre',String(resultById.get(row.task_id).result)):null,
        h('button.bx-btn',{type:'button',onClick:()=>ctx.navigate('video-studio',{project_id:row.project_id})},'Открыть Video Studio')))));
  },onEvent:()=>false};

export function mountVideoTabs(ctx,view) {
  const bar=h('nav',{'aria-label':'Рабочие вкладки',style:{display:'flex',gap:'8px',padding:'8px 0'}},
    h('button.bx-btn',{type:'button',onClick:()=>ctx.navigate('bossman-chat')},'Bossman Chat'),
    h('button.bx-btn',{type:'button',onClick:()=>ctx.navigate('video-studio',state.lastProject?{project_id:state.lastProject}:null)},'Video Studio'));
  view.parentNode.insertBefore(bar,view);
  window.addEventListener('bcc:video-open',event=>{state.lastProject=event.detail.project_id;});
  const remember=()=>{if(location.hash.startsWith('#/video-studio?'))state.lastProject=new URLSearchParams(location.hash.split('?')[1]).get('project_id')||state.lastProject;};
  window.addEventListener('hashchange',remember);remember();
}
