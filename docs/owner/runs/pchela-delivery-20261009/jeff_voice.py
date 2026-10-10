import subprocess,tempfile,os,json,time
from pathlib import Path
from bcc.oss.chatterbox_clone import synthesize_ogg
PY=r'C:\Users\asd\Bossman\voice-p4ela\venv\Scripts\python.exe'; MD=r'C:\Users\asd\Bossman\voice-p4ela\chatterbox'
FF=r'C:\Users\asd\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe'
REF=r'C:\Users\asd\Bossman\pchela-delivery-20261009\jeff_ref_20261009.wav'
TEXT=["Привет! Это Джефф, ИИ-ассистент Боссмана. Сегодня мы закончили обучение вашего голоса и лица: две модели готовы, и я уже проверил, что они работают.",
"Фото-модель узнаёт вас на всех тестовых сценах, а голос стал чуть ближе к оригиналу, но настоящий вердикт за вашими ушами. Послушайте и скажите, что подправить.",
"Дальше я соберу всё в единый Боссман, прогоню проверку интерфейса от вашего лица и обновлю дерево возможностей. Пока меня не остановят, я продолжаю работать."]
parts=[];t0=time.time()
for i,t in enumerate(TEXT):
    b=synthesize_ogg(t,python_executable=PY,model_dir=MD,reference_path=REF,ffmpeg_executable=FF)
    Path(f'jeff_part{i}.ogg').write_bytes(b); parts.append(f'jeff_part{i}.ogg'); print('part',i,len(b),round(time.time()-t0),'s',flush=True)
open('jeff_concat.txt','w').write(''.join(f"file '{p}'\n" for p in parts))
subprocess.run([FF,'-v','error','-y','-f','concat','-safe','0','-i','jeff_concat.txt','-c:a','libopus','-b:a','32k','jeff_speech.ogg'],check=True)
print('done',os.path.getsize('jeff_speech.ogg'))
