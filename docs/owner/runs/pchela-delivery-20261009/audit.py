import json,hashlib,subprocess,base64,io,glob,re,urllib.request,os
from PIL import Image
from bcc_call import c
FF=r'C:\Users\asd\AppData\Local\Microsoft\WinGet\Links\ffprobe.exe'
jobs=json.load(open('hq_jobs.json')); rep={'checks':[]}
sha=lambda p:hashlib.sha256(open(p,'rb').read()).hexdigest()
def chk(name,ok,detail): rep['checks'].append({'name':name,'ok':bool(ok),'detail':detail}); print(('PASS ' if ok else 'FAIL ')+name,'|',detail,flush=True)
for k in ('A','B'):
    j=c.get(f'/api/direct-gen/jobs/{jobs[k]}'); p=f'photo{k}_40steps.png'; im=Image.open(p)
    s=j.get('settings') or j.get('params') or {}
    chk(f'photo{k} decodes 1024x1024',im.size==(1024,1024),f'{im.size} sha256 {sha(p)[:16]}')
    chk(f'photo{k} job record: model epicrealism-xl, status completed, LoRA tag in raw prompt, steps',j['model']=='epicrealism-xl' and j['status']=='completed' and '<lora:pchela-lora' in j['raw_prompt'] and (s.get('steps')==40),f"steps={s.get('steps')} seed={s.get('seed')} prompt_verbatim={j.get('effective_prompt')==j.get('raw_prompt')}")
v=c.get(f"/api/direct-gen/jobs/{jobs['video']}"); vp=glob.glob(f"data/direct-gen/participants/owner/jobs/{jobs['video']}/result/video.*")[0]
pr=json.loads(subprocess.run([FF,'-v','error','-show_entries','stream=codec_name,width,height,nb_frames,r_frame_rate:format=duration','-of','json',vp],capture_output=True,text=True).stdout)
chk('video 4 s',abs(float(pr['format']['duration'])-4.0)<0.15,f"{pr['format']['duration']}s {pr['streams'][0]['width']}x{pr['streams'][0]['height']} {pr['streams'][0]['codec_name']} frames={pr['streams'][0].get('nb_frames')} model={v['model']}")
sp=json.loads(subprocess.run([FF,'-v','error','-show_entries','format=duration','-of','json','jeff_speech.ogg'],capture_output=True,text=True).stdout)
chk('speech >= 30 s, ogg/opus',float(sp['format']['duration'])>=30 and open('jeff_speech.ogg','rb').read(4)==b'OggS',f"{sp['format']['duration']}s sha256 {sha('jeff_speech.ogg')[:16]}")
rep['files']={p:sha(p) for p in (f'photoA_40steps.png',f'photoB_40steps.png',vp,'jeff_speech.ogg')}
json.dump(rep,open('audit_report.json','w'),ensure_ascii=False,indent=1)
