import json,time,base64,shutil,os,psutil
from bcc_call import c
neg="blurry, deformed, extra fingers, text, watermark, lowres"
P={"A":("pchela, close-up portrait photo of a young man with short buzzed hair, grey hoodie, autumn park, natural daylight, sharp focus <lora:pchela-lora:0.8>",2026),
   "B":("pchela, young man in a dark suit on a rainy city street at night, neon reflections, cinematic lighting, film grain <lora:pchela-lora:0.8>",2027)}
def idle():
    for _ in range(120):
        if not any((p.info['name'] or '').lower()=='sd-cli.exe' for p in psutil.process_iter(['name'])): return
        time.sleep(2)
def submit(body):
    for a in range(6):
        idle(); r=c.post('/api/direct-gen/jobs',body); f=wait(r['job_id'])
        if not (f['status']=='failed' and 'gpu_busy' in str(f.get('error'))): return r['job_id'],f
        print('gpu_busy, retry',a+1,flush=True); time.sleep(5)
    return r['job_id'],f
def wait(j):
    while True:
        r=c.get(f'/api/direct-gen/jobs/{j}')
        if r['status'] in('completed','failed','cancelled'): return r
        time.sleep(10)
out={}
for k,(pr,seed) in P.items():
    t=time.time(); j,f=submit({"model":"epicrealism-xl","prompt":pr,"negative":neg,"resolution":"1024x1024","seed":seed,"steps":40,"duration":1}); print(k,f['status'],round(time.time()-t),'s',f.get('error'),flush=True)
    src=f'data/direct-gen/participants/owner/jobs/{j}/result/image.png'; shutil.copy(src,f'photo{k}_40steps.png'); out[k]=j
    time.sleep(3)
img=base64.b64encode(open('photoA_40steps.png','rb').read()).decode()
t=time.time(); j,f=submit({"model":"wan2.2-ti2v-5b","mode":"DIRECT","prompt":"pchela, young man with short buzzed hair in a grey hoodie in an autumn park, subtle natural head movement, blinks, slight smile, leaves moving in the wind","negative":"blurry, deformed, distorted face, text, watermark","duration":4,"resolution":"448x448","seed":2028,"image_b64":img})
print('video',f['status'],round(time.time()-t),'s',f.get('result'),flush=True); out['video']=j
json.dump(out,open('hq_jobs.json','w')); print('HQ_DONE')
