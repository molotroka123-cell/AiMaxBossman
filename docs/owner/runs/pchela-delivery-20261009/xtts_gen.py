import os,glob,torch,torchaudio,soundfile as sf
from TTS.tts.configs.xtts_config import XttsConfig
from TTS.tts.models.xtts import Xtts
run=sorted(glob.glob('/workspace/xtts_run/xtts_pchela-*'))[-1]
best=sorted(glob.glob(run+'/best_model*.pth'))[-1]; print('best',best)
base='/workspace/xtts_run/base'
ref=[l.split('|')[0] for l in open('/workspace/voice_ds/metadata.csv',encoding='utf-8').read().split('\n')[1:] if l][5]
ref='/workspace/voice_ds/'+ref
phr=['Привет! Это проверка моего голоса после обучения. Как слышно?','Сегодня хороший день, чтобы закончить все дела и отдохнуть.','Босс на связи, задача принята, приступаю к работе.']
os.makedirs('/workspace/gen_voice',exist_ok=True)
for tag,ck in (('base',base+'/model.pth'),('ft',best)):
    cfg=XttsConfig(); cfg.load_json(base+'/config.json')
    m=Xtts.init_from_config(cfg); m.load_checkpoint(cfg,checkpoint_path=ck,vocab_path=base+'/vocab.json',use_deepspeed=False); m.cuda().eval()
    gl,sp=m.get_conditioning_latents(audio_path=[ref])
    for i,t in enumerate(phr):
        o=m.inference(t,'ru',gl,sp,temperature=0.7)
        sf.write(f'/workspace/gen_voice/{tag}_{i}.wav',o['wav'],24000); print('done',tag,i,flush=True)
    del m; torch.cuda.empty_cache()
print('VGEN_DONE')
