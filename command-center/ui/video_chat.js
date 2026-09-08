import { api } from './api.js';
import { h, toastError, toastOk } from './components.js';
const state = { text: '', files: [], requestId: '', lastProject: '', requestKey:'', uploaded:new Set(),agentId:'' };
const newId = () => crypto.randomUUID();
export async function routeVideoRequest(text, files, ctx) {
  const key=JSON.stringify([text,files.map(f=>[f.name,f.size,f.lastModified])]);
  if (!state.requestId || state.requestKey!==key) {state.requestId=newId();state.requestKey=key;state.uploaded.clear();}
  const payload={text,operation_id:state.requestId};
  if(state.lastProject && /^(?:открой|open)(?=\s|$)/i.test(text.trim())) payload.project_id=state.lastProject;
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
  try {
    await api.raw(`/api/video-studio/chat/${response.task_id}/run`,{method:'POST'});
  } catch (error) {
    // The draft/project survived admission refusal. Expose a direct way to
    // attach media or make explicit edits, without retrying the rejected task.
    error.videoProjectId=response.project_id;
    throw error;
  }
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
export const ChatPage={id:'bossman-chat',title:'История видео и чат',icon:'terminal',section:'studio',nav:'more',
  async render(ctx) {
    let records=[];
    try {records=(await api.raw('/api/video-studio/chat')).messages||[];}catch(e){toastError(e);}
    let agents=[];
    try {const result=await api.agents();agents=(Array.isArray(result)?result:result.agents||[]).filter(a=>a.enabled!==false);}catch(e){toastError(e);}
    const agentSelect=h('select',{'aria-label':'Агент для обычного вопроса'},
      h('option',{value:''},'Выберите агента для обычных вопросов'),
      ...agents.map(a=>h('option',{value:String(a.id),selected:String(a.id)===state.agentId},a.name||`Агент ${a.id}`)));
    agentSelect.addEventListener('change',()=>{state.agentId=agentSelect.value;});
    const taskResults=await Promise.allSettled(records.slice(0,20).map(row=>api.task(row.task_id)));
    const resultById=new Map(records.slice(0,20).map((row,i)=>[row.task_id,taskResults[i].status==='fulfilled'?taskResults[i].value:null]));
    const input=h('textarea',{rows:4,placeholder:'Склей эти два видео',value:state.text,
      'aria-label':'Задание для Video Studio'});
    input.addEventListener('input',()=>{state.text=input.value;state.requestId='';});
    const files=attachmentInput();
    const status=h('p',{role:'status'},'Файлы остаются локально. Теоретические вопросы не создают проект.');
    const recovery=h('div');
    const send=h('button.bx-btn.bx-btn-primary',{type:'button'},'Отправить');
    send.addEventListener('click',async()=>{
      send.disabled=true;
      recovery.replaceChildren();
      try {
        if(!await routeVideoRequest(input.value.trim(),state.files,ctx)) {
          if(!state.agentId) {status.textContent='Выберите существующего агента для ответа на обычный вопрос.';return;}
          if(state.files.length) {status.textContent='Вложения сохранены. Для обычного вопроса сначала уберите медиа: они не отправляются общему агенту автоматически.';return;}
          const text=input.value.trim();
          if(!text) return;
          await api.createTask({title:text.split('\n')[0].slice(0,100),prompt:text,agent_id:Number(state.agentId),priority:5,run_now:true});
          state.text='';input.value='';toastOk('Вопрос отправлен выбранному агенту');ctx.navigate('tasks');
        }
      } catch(e){
        toastError(e);status.textContent=e.message;
        if(e.videoProjectId) recovery.append(h('button.bx-btn',{type:'button',
          onClick:()=>ctx.navigate('video-studio',{project_id:e.videoProjectId})},'Открыть сохранённый проект'));
      }
      finally{send.disabled=false;}
    });
    return h('section.bx-panel',h('div.bx-panel-body',h('h2','Bossman Chat'),input,files,agentSelect,send,status,recovery,
      ...records.map(row=>h('article.bx-panel',h('p',row.text),h('p',`Задача #${row.task_id} · ${resultById.get(row.task_id)?.task?.status||'сохранена'}`),
        resultById.get(row.task_id)?.error?h('p',{role:'alert'},String(resultById.get(row.task_id).error)):null,
        resultById.get(row.task_id)?.result?h('pre',String(resultById.get(row.task_id).result)):null,
        h('button.bx-btn',{type:'button',onClick:()=>ctx.navigate('video-studio',{project_id:row.project_id})},'Открыть Video Studio')))));
  },onEvent:()=>false};

// One editor, registered in the shared navigation. Keep project continuity
// without mounting a second navigation shell above every application.
export function trackVideoProject(target = window, getHash = () => location.hash) {
  const remember = () => {
    const [route, query = ''] = String(getHash()).split('?');
    if (route !== '#/video-studio') return;
    const id = new URLSearchParams(query).get('project_id');
    if (id) state.lastProject = id;
  };
  const opened = event => {
    const id = event.detail?.project_id;
    if (typeof id === 'string' && id) state.lastProject = id;
  };
  target.addEventListener('bcc:video-open', opened);
  target.addEventListener('hashchange', remember);
  remember();
  return () => {
    target.removeEventListener('bcc:video-open', opened);
    target.removeEventListener('hashchange', remember);
  };
}
