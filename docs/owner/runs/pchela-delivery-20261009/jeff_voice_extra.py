import subprocess,os
from pathlib import Path
from bcc.oss.chatterbox_clone import synthesize_ogg
PY=r'C:\Users\asd\Bossman\voice-p4ela\venv\Scripts\python.exe'; MD=r'C:\Users\asd\Bossman\voice-p4ela\chatterbox'
FF=r'C:\Users\asd\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe'; REF=r'C:\Users\asd\Bossman\pchela-delivery-20261009\jeff_ref_20261009.wav'
t="Файлы я пришлю сюда, в пульт. Если что-то прозвучит не совсем естественно, это нормально для первой версии: дальше будем улучшать."
Path('jeff_part3.ogg').write_bytes(synthesize_ogg(t,python_executable=PY,model_dir=MD,reference_path=REF,ffmpeg_executable=FF))
open('jeff_concat.txt','w').write(''.join(f"file 'jeff_part{i}.ogg'\n" for i in range(4)))
subprocess.run([FF,'-v','error','-y','-f','concat','-safe','0','-i','jeff_concat.txt','-c:a','libopus','-b:a','32k','jeff_speech.ogg'],check=True)
print(os.path.getsize('jeff_speech.ogg'))
