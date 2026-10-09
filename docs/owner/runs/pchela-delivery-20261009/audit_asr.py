import re,json,subprocess
from faster_whisper import WhisperModel
subprocess.run([r'C:\Users\asd\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe','-v','error','-y','-i','jeff_speech.ogg','-ar','16000','-ac','1','jeff_speech_16k.wav'],check=True)
ref=open('jeff_voice.py',encoding='utf-8').read(); import ast
txt=' '.join(ast.literal_eval(re.search(r'TEXT=(\[.*?\])\n',ref,re.S).group(1)))+' Файлы я пришлю сюда, в пульт. Если что-то прозвучит не совсем естественно, это нормально для первой версии: дальше будем улучшать.'
norm=lambda s:re.sub(r'[^\w\s]','',s.lower()).split()
m=WhisperModel(r'C:\Users\asd\Bossman\models\voice\whisper\faster-whisper-large-v3-turbo',device='cpu',compute_type='int8',cpu_threads=16)
segs,_=m.transcribe('jeff_speech_16k.wav',language='ru',beam_size=5); h=' '.join(s.text for s in segs)
r,hh=norm(txt),norm(h); d=[[0]*(len(hh)+1) for _ in range(len(r)+1)]
for i in range(len(r)+1): d[i][0]=i
for j in range(len(hh)+1): d[0][j]=j
for i in range(1,len(r)+1):
    for j in range(1,len(hh)+1): d[i][j]=min(d[i-1][j]+1,d[i][j-1]+1,d[i-1][j-1]+(r[i-1]!=hh[j-1]))
print('WER',round(d[-1][-1]/len(r),3),'ref words',len(r),'hyp words',len(hh))
json.dump({'wer':round(d[-1][-1]/len(r),3),'hyp':h},open('audit_asr.json','w'),ensure_ascii=False)
