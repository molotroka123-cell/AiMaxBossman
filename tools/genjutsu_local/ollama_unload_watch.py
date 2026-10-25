"""While WanGP runs: any model loaded in Ollama is unloaded with keep_alive=0 (coordinator-allowed 25.10). Stops when no wgp.py python is alive for 2 checks."""
import json, time, urllib.request, subprocess, datetime
LOG = r"D:\Bossman\work\genjutsu-local-20261025\logs\ollama_unload.log"
def log(s):
    open(LOG, "a", encoding="utf-8").write(f"{datetime.datetime.now():%H:%M:%S} {s}\n")
def wgp_alive():
    out = subprocess.run(["powershell", "-NoProfile", "-c", "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ? { $_.CommandLine -like '*wgp.py*' }).Count"], capture_output=True, text=True).stdout.strip()
    return out not in ("", "0")
miss = 0; log("start")
time.sleep(60)
while True:
    try:
        ps = json.load(urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=10)).get("models", [])
        for m in ps:
            body = json.dumps({"model": m["name"], "keep_alive": 0}).encode()
            urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:11434/api/generate", data=body, headers={"Content-Type": "application/json"}), timeout=60).read()
            log(f"unloaded {m['name']} ({round(m.get('size',0)/1e9,1)} GB)")
    except Exception as e:
        log(f"err {type(e).__name__}")
    miss = 0 if wgp_alive() else miss + 1
    if miss >= 2: log("wgp gone -> stop"); break
    time.sleep(15)
