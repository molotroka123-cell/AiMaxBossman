import sys, time, re; sys.path.insert(0, r'C:\Users\asd\Bossman\wt-trio-claude\tools'); from prove_chat_attachments import api
img = sys.argv[1]
q=("Это снимок экрана ПК. Ответь ОДНОЙ строкой строго в формате и больше ничего: "
   "«ПРИЛОЖЕНИЕ: <имя приложения на переднем плане>; TELEGRAM_ВИДЕН: да|нет; TELEGRAM_СОСТОЯНИЕ: список_чатов|открытый_чат|открытый_канал_с_постом|экран_входа|нет_доступа|не_виден|другое; ВИДЕО_В_КАДРЕ: да|нет»")
th=api("POST","/api/chat/threads",{"title":"PROOF screen diag"}); tid=th.get("id")
up=api("POST",f"/api/chat/attachments?filename=s.png&thread_id={tid}",raw=open(img,"rb").read(),ctype="application/octet-stream")
s=api("POST",f"/api/chat/threads/{tid}/send",{"text":q,"agent_id":41,"attachments":[up["id"]]},timeout=900)
task=s.get("task_id") or (s.get("task") or {}).get("id"); t0=time.time()
while time.time()-t0<300:
    v=api("GET",f"/api/tasks/{task}")
    if (v.get("task") or {}).get("status") in ("completed","failed"): break
    time.sleep(5)
ans=str(v.get("result") or "").strip().splitlines()[0] if v.get("result") else ""
m=re.search(r"ПРИЛОЖЕНИЕ:\s*([^;]{0,30});\s*TELEGRAM_ВИДЕН:\s*(да|нет);\s*TELEGRAM_СОСТОЯНИЕ:\s*([a-zа-я_]{0,30});\s*ВИДЕО_В_КАДРЕ:\s*(да|нет)",ans,re.I)
print("parsed:", m.groups() if m else "(format not matched)")
api("DELETE",f"/api/chat/threads/{tid}?purge=true&confirm={tid}")
