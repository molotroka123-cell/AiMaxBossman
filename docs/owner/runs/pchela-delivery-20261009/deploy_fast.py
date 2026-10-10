import json,urllib.request,time,subprocess,datetime,os,sys
k=[l.split('=',1)[1].strip() for l in open('.runpod.env') if l.startswith('RUNPOD_API_KEY=')][0]
def gql(q):
    r=urllib.request.Request('https://api.runpod.io/graphql',data=json.dumps({'query':q}).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+k,'User-Agent':'bossman-audit/1'})
    return json.load(urllib.request.urlopen(r,timeout=60))
def term(pid): return gql('mutation{podTerminate(input:{podId:"%s"})}'%pid)
old=open('pod_id.txt').read().strip(); print('terminate old',term(old))
ssh=lambda H,P,cmd,t=90: subprocess.run(['ssh','-i','id_pchela','-p',str(P),'-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-o','BatchMode=yes','-o','ConnectTimeout=15',f'root@{H}',cmd],capture_output=True,text=True,timeout=t)
cands=[('SECURE','NVIDIA GeForce RTX 4090'),('COMMUNITY','NVIDIA GeForce RTX 4090'),('SECURE','NVIDIA GeForce RTX 3090'),('COMMUNITY','NVIDIA GeForce RTX 3090'),('COMMUNITY','NVIDIA RTX A5000'),('SECURE','NVIDIA RTX A5000'),('COMMUNITY','NVIDIA GeForce RTX 3090 Ti'),('SECURE','NVIDIA A40')]
for round_ in range(3):
  for cloud,gpu in cands:
    q='''mutation{podFindAndDeployOnDemand(input:{cloudType:%s,gpuCount:1,volumeInGb:50,containerDiskInGb:30,minVcpuCount:4,minMemoryInGb:24,gpuTypeId:"%s",name:"pchela-lora-fast",imageName:"runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04",ports:"22/tcp",volumeMountPath:"/workspace",startSsh:true,supportPublicIp:true}){id costPerHr}}'''%(cloud,gpu)
    r=gql(q); p=(r.get('data') or {}).get('podFindAndDeployOnDemand')
    if not p: continue
    pid=p['id']; print('deployed',cloud,gpu,p,flush=True)
    target=None
    for i in range(60):
        d=gql('query{pod(input:{podId:"%s"}){runtime{ports{ip publicPort privatePort isIpPublic}}}}'%pid)['data']['pod']
        s=[x for x in ((d.get('runtime') or {}).get('ports') or []) if x['privatePort']==22 and x['isIpPublic']]
        if s: target=(s[0]['ip'],s[0]['publicPort']); break
        time.sleep(10)
    ok=False
    if target:
        for i in range(12):
            try:
                o=ssh(*target,"timeout 25 curl -s -o /dev/null -w '%{speed_download}' -r 0-40000000 -L https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors; echo; uptime | sed 's/.*average//'; nvidia-smi --query-gpu=name --format=csv,noheader",120)
                if o.returncode==0:
                    sp=float(o.stdout.split()[0]); print('speed MB/s',round(sp/1e6,1),o.stdout.split('\n')[1:3],flush=True); ok=sp>8e6; break
            except Exception as e: pass
            time.sleep(10)
    if ok:
        open('pod_id.txt','w').write(pid); open('ssh_target.txt','w').write(f'{target[0]} {target[1]}'); open('gpu.txt','w').write(f'{cloud} {gpu} {p["costPerHr"]}'); print('GOOD',target); sys.exit(0)
    print('bad host, terminating',pid,term(pid),flush=True)
print('no good host'); sys.exit(1)
