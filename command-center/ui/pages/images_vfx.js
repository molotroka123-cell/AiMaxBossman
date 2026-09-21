/* Use the existing Studio prompt and input event; never submit a provider job. */
import base from './images.js';
import {shotRecipePanel} from './viral_vfx_panel.js';
import {toastError} from '../components.js';
export default {...base,async render(ctx,params={}){
  const root=await base.render(ctx,params);
  const surface=root.querySelector('.studio-surface');
  if(surface)surface.prepend(shotRecipePanel(text=>{
    const prompt=surface.querySelector('.studio-prompt');
    if(!prompt||!root.isConnected){toastError(new Error('Откройте Studio заново.'));return;}
    prompt.value=text;prompt.dispatchEvent(new Event('input',{bubbles:true}));prompt.focus();
  }));
  return root;
}};
