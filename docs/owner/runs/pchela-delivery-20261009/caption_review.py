import base64,glob,hashlib,io,json,os,random,re,time,urllib.request
from PIL import Image
KEY=<read from the Bossman key store, never printed>
MODEL='anthropic/claude-haiku-5.5'; CAP_USD=0.5; spent=0.0; PI,PO=0.10/1e6,0.50/1e6
SRC='stage/10_pchela'; OUT='stage_final/10_pchela'; os.makedirs(OUT,exist_ok=True)
def call(content,max_tokens=400):
    global spent
    if spent>CAP_USD: raise SystemExit('cost cap reached')
    body=json.dumps({'model':MODEL,'messages':[{'role':'user','content':content}],'max_tokens':max_tokens,'temperature':0}).encode()
    req=urllib.request.Request('https://openrouter.ai/api/v1/chat/completions',data=body,headers={'Authorization':'Bearer '+KEY,'Content-Type':'application/json','User-Agent':'bossman/1'})
    for a in range(3):
        try:
            d=json.load(urllib.request.urlopen(req,timeout=90)); break
        except Exception as e:
            if a==2: raise
            time.sleep(3)
    u=d.get('usage',{}); spent+=u.get('prompt_tokens',0)*PI+u.get('completion_tokens',0)*PO
    c=d['choices'][0]['message']['content']
    return c if isinstance(c,str) else ('' if c is None else ''.join(b.get('text','') for b in c if isinstance(b,dict)))
def img_part(p):
    im=Image.open(p).convert('RGB'); im.thumbnail((768,768)); b=io.BytesIO(); im.save(b,'JPEG',quality=85)
    return {'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+base64.b64encode(b.getvalue()).decode()}}
seen={}; files=[]
for p in sorted(glob.glob(SRC+'/*.jpg')+glob.glob(SRC+'/*.png')):
    h=hashlib.sha256(open(p,'rb').read()).hexdigest()
    if h in seen: continue
    seen[h]=p; files.append(p)
print('unique photos',len(files))
GEN=("You write training captions for an SDXL LoRA of one person. Look at the photo and write ONE caption: start with the trigger word 'pchela', "
     "then comma-separated factual phrases: shot type/framing, hair, clothing, expression, pose/action, setting, lighting. Only what is clearly visible; "
     "no guesses about identity, age numbers, emotions beyond visible expression, or things not shown. 12-35 words. Output the caption only.")
JUDGE=("Two captions (A and B) for the same training photo. Pick the one that is more accurate to what is visible, more specific, and free of invented details; "
       "both must keep the trigger 'pchela' first. Reply JSON only: {\"winner\":\"A\"|\"B\",\"reason\":\"<12 words\",\"errors_A\":[...],\"errors_B\":[...]}")
res=[]
for i,p in enumerate(files):
    stem=os.path.splitext(p)[0]; old=open(stem+'.txt',encoding='utf-8').read().strip()
    new=call([img_part(p),{'type':'text','text':GEN}],1500).strip().strip('"')
    if not new.lower().startswith('pchela'): new='pchela, '+new
    rnd=random.Random(hashlib.sha256(p.encode()).digest()); swap=rnd.random()<0.5
    A,B=(new,old) if swap else (old,new)
    j=call([img_part(p),{'type':'text','text':f'{JUDGE}\n\nA: {A}\nB: {B}'}],300)
    m=re.search(r'\{.*\}',j,re.S)
    try: v=json.loads(m.group(0))
    except Exception: v={'winner':None,'reason':'unparsed','raw':j[:200]}
    w=v.get('winner'); chosen=old
    if w in('A','B'): chosen=(A if w=='A' else B)
    src='haiku' if chosen==new and chosen!=old else 'existing'
    open(os.path.join(OUT,os.path.basename(stem)+'.txt'),'w',encoding='utf-8',newline='\n').write(chosen+'\n')
    import shutil; shutil.copy(p,os.path.join(OUT,os.path.basename(p)))
    res.append({'file':os.path.basename(p),'existing':old,'haiku':new,'winner':src,'judge':v,'spent':round(spent,4)})
    print(i+1,os.path.basename(p),src,round(spent,4),flush=True)
json.dump(res,open('caption_review.json','w',encoding='utf-8'),ensure_ascii=False,indent=1)
print('haiku chosen',sum(r['winner']=='haiku' for r in res),'existing chosen',sum(r['winner']=='existing' for r in res),'spent $',round(spent,4))
