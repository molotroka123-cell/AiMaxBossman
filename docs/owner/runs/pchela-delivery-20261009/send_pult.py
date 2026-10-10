import asyncio,re,ast
from pathlib import Path
from bcc.telegram_companion.owner_report import _load_owner
from bcc.telegram_companion.adapters import Telegram
src=open('jeff_voice.py',encoding='utf-8').read()
TEXT=ast.literal_eval(re.search(r'TEXT=(\[.*?\])\n',src,re.S).group(1))+["Файлы я пришлю сюда, в пульт. Если что-то прозвучит не совсем естественно, это нормально для первой версии: дальше будем улучшать."]
full=' '.join(TEXT)
async def main():
    settings,owner=_load_owner(None); t=Telegram(settings); ids={}
    try:
        ogg=Path('jeff_speech.ogg').read_bytes()
        async def synth(_): return ogg
        ids['voice']=await t.send_voice(owner,full,synth)
        ids['photoA']=await t.send_photo(owner,Path('photoA_40steps.png').read_bytes(),"Фото 1 (фотореал): epiCRealism XL + LoRA «пчела» 0,8, 40 шагов, seed 2026. Сгенерировано ИИ через Bossman direct_gen.")
        ids['photoB']=await t.send_photo(owner,Path('photoB_40steps.png').read_bytes(),"Фото 2 (кино-стиль, ночной город): та же модель и LoRA, 40 шагов, seed 2027. Сгенерировано ИИ через Bossman direct_gen.")
        ids['video']=await t.send_video(owner,Path('pchela_video_4s.mp4').read_bytes(),"Видео 4 с: Wan2.2-TI2V-5B из фото 1, 448×448, 16 к/с, 20 шагов, seed 2028. Сгенерировано ИИ через Bossman direct_gen.")
    finally:
        await t.close()
    print(ids)
asyncio.run(main())
