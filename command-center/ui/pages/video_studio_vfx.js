/* Decorate, never replace, the canonical editor. Toolbar is outside repaint root. */
import base from './video_studio.js';
import {h,toastError} from '../components.js';
import {openVfxPanel} from './viral_vfx_panel.js';
let shell=null,editorRoot=null;
export default {...base,async render(ctx,params={}){
  const root=await base.render(ctx,params);
  if(shell?.isConnected&&editorRoot===root)return shell;
  editorRoot=root;
  shell=h('div.stack.sm',h('div.row',h('button.btn',{type:'button',onClick:()=>openVfxPanel(root,ctx).catch(e=>toastError(e))},'Viral VFX · Phonk'),h('span.small.dim','Выберите клип → проверьте план → примените. Preview и экспорт — в этом редакторе.')),root);
  return shell;
}};
