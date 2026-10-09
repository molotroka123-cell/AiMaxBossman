import json,os,subprocess,csv,sys,time
from faster_whisper import WhisperModel
SRC=r'C:\Bossman\lora-dataset-backup\face-dataset\voice_sample_clean.wav'
OUT='voice_dataset_v2'; W=os.path.join(OUT,'wavs'); os.makedirs(W,exist_ok=True)
t=time.time()
m=WhisperModel(r'C:\Users\asd\Bossman\models\voice\whisper\faster-whisper-large-v3-turbo',device='cpu',compute_type='int8',cpu_threads=16)
segs,info=m.transcribe(SRC,vad_filter=True,vad_parameters=dict(min_silence_duration_ms=300),word_timestamps=True,beam_size=5,condition_on_previous_text=False)
words=[]
for s in segs:
    for w in (s.words or []): words.append((w.start,w.end,w.word,w.probability))
print('lang',info.language,round(info.language_probability,3),'words',len(words),'asr_s',round(time.time()-t))
chunks=[];cur=None
def close():
    global cur
    if cur: chunks.append(cur); cur=None
for i,(st,en,wd,pr) in enumerate(words):
    gap=(st-words[i-1][1]) if i else 0
    if cur and (gap>0.9 or (en-cur['start'])>15): close()
    if not cur: cur=dict(start=st,end=en,text='',lp=[],ns=0.0)
    cur['end']=en; cur['text']+=wd; cur['lp'].append(__import__('math').log(max(pr,1e-6)))
    if wd.strip().endswith(('.','?','!')) and (en-cur['start'])>=6: close()
close()
for c in chunks: c['text']=c['text'].strip()
keep=[];drop=[]
for c in chunks:
    d=c['end']-c['start']; lp=sum(c['lp'])/len(c['lp'])
    (keep if (d>=3.0 and lp>-0.9 and c['ns']<0.5 and d<=16) else drop).append(dict(start=round(c['start'],2),end=round(c['end'],2),dur=round(d,2),text=c['text'],avg_logprob=round(lp,3)))
rows=[]
for i,c in enumerate(keep):
    f=f'pchela_{i:03d}.wav'
    subprocess.run(['ffmpeg','-v','error','-y','-ss',str(c['start']),'-to',str(c['end']),'-i',SRC,'-ac','1','-ar','22050','-c:a','pcm_s16le',os.path.join(W,f)],check=True)
    rows.append((f'wavs/{f}',c['text'],'pchela'))
with open(os.path.join(OUT,'metadata.csv'),'w',encoding='utf-8',newline='') as fh:
    w=csv.writer(fh,delimiter='|'); w.writerow(['audio_file','text','speaker_name']); w.writerows(rows)
json.dump(dict(language=info.language,kept=keep,dropped=drop),open(os.path.join(OUT,'annotation.json'),'w',encoding='utf-8'),ensure_ascii=False,indent=1)
print('kept',len(keep),'min',round(sum(c['dur'] for c in keep)/60,2),'dropped',len(drop),'min',round(sum(c['dur'] for c in drop)/60,2))
