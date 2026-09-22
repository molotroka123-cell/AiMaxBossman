"""START_TOMORROW_RU step 6 driver: real sd.cpp generation through the installed Studio API.
Writes step6.log / step6.json next to this file. Evidence = files, ffprobe, provenance; no claims."""
import json, subprocess, sys, time, urllib.request, hashlib, pathlib

BASE = "http://127.0.0.1:8810"
HERE = pathlib.Path(__file__).parent
TOKEN = (HERE / "data" / "token").read_text().strip()
FFPROBE = str(next((HERE).glob("BOSSMAN-*/media/ffprobe.exe"), "ffprobe"))
FFMPEG = str(next((HERE).glob("BOSSMAN-*/media/ffmpeg.exe"), "ffmpeg"))
LOG = open(HERE / "step6.log", "a", encoding="utf-8")
OUT = {}


def log(*a):
    line = time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a)
    print(line, file=LOG, flush=True)


def api(method, path, body=None, raw=False):
    req = urllib.request.Request(BASE + path, method=method, headers={"X-BCC-Token": TOKEN, "Content-Type": "application/json"},
                                 data=None if body is None else json.dumps(body).encode())
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    return data if raw else json.loads(data)


STATE = HERE / "step6-state.json"


def submit(model, prompt, settings, media=None, key=None):
    st = json.loads(STATE.read_text()) if STATE.exists() else {}
    if key and key in st:
        log("resuming", key, "job", st[key])
        return st[key]
    body = {"model": model, "prompt": prompt, "settings": settings}
    if media:
        body["media"] = media
    j = api("POST", "/api/studio/jobs", body)
    log("submitted", model, j["id"], settings)
    if key:
        st[key] = j["id"]
        STATE.write_text(json.dumps(st))
    return j["id"]


def wait(jid, limit_s=None):
    t0 = time.time()
    while True:
        j = api("GET", f"/api/studio/jobs/{jid}")
        if j["status"] in ("completed", "failed", "canceled", "cancelled", "interrupted_unknown"):
            j["_elapsed_s"] = round(time.time() - t0, 1)
            return j
        time.sleep(10)


def runs_of(jid):
    return [r for r in api("GET", "/api/studio/runs?limit=200")["items"] if r.get("job_id") == jid]


def check_file(run, name):
    data = api("GET", run["file_url"], raw=True)
    p = HERE / "step6-out" / name
    p.parent.mkdir(exist_ok=True)
    p.write_bytes(data)
    info = {"path": str(p), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    pr = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "stream=codec_name,width,height,nb_frames,avg_frame_rate:format=duration",
                         "-of", "json", str(p)], capture_output=True, text=True)
    info["ffprobe_rc"], info["ffprobe"] = pr.returncode, (json.loads(pr.stdout) if pr.returncode == 0 else pr.stderr[-500:])
    dec = subprocess.run([FFMPEG, "-v", "error", "-i", str(p), "-f", "null", "-"], capture_output=True, text=True)
    info["full_decode_rc"], info["decode_errors"] = dec.returncode, dec.stderr[-500:]
    prov = run.get("provenance") or {}
    info["provenance_keys"] = sorted(prov)[:40]
    info["engine_trace"] = prov.get("engine_trace") if isinstance(prov.get("engine_trace"), dict) else None
    return info


def sdcli_procs():
    r = subprocess.run(["tasklist", "/fi", "imagename eq sd-cli.exe", "/fo", "csv", "/nh"], capture_output=True, text=True)
    return [l for l in r.stdout.splitlines() if "sd-cli" in l]


def step(name, fn):
    log("=== ", name)
    try:
        OUT[name] = fn()
    except Exception as exc:  # record, continue with the next step
        OUT[name] = {"error": f"{type(exc).__name__}: {exc}"}
    log(name, "->", json.dumps(OUT[name], ensure_ascii=False, default=str)[:1500])
    (HERE / "step6.json").write_text(json.dumps(OUT, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def image():
    jid = submit("sdcpp:z-image-turbo", "a red fox in a snowy birch forest, morning light, photo", {"width": 1024, "height": 1024, "seed": 7}, key="image")
    j = wait(jid)
    res = {"job": {k: j.get(k) for k in ("id", "status", "error", "_elapsed_s")}}
    rs = runs_of(jid)
    if rs:
        res["file"] = check_file(rs[0], "image.png")
        res["run_id"] = rs[0]["id"]
    return res


def t2v():
    jid = submit("sdcpp:wan2.2-ti2v-5b", "a paper boat drifting on a calm lake, gentle ripples, cinematic",
                 {"width": 832, "height": 480, "frames": 49, "fps": 16, "steps": 20, "seed": 7}, key="t2v")
    j = wait(jid)
    res = {"job": {k: j.get(k) for k in ("id", "status", "error", "_elapsed_s")}}
    rs = runs_of(jid)
    if rs:
        res["file"] = check_file(rs[0], "t2v.mp4")
    return res


def i2v():
    rid = OUT.get("image", {}).get("run_id")
    if not rid:
        return {"skipped": "no image run"}
    jid = submit("sdcpp:wan2.2-ti2v-5b", "the fox turns its head and snow falls softly",
                 {"width": 832, "height": 480, "frames": 49, "fps": 16, "steps": 20, "seed": 7},
                 media=[{"run_id": rid, "role": "start"}], key="i2v")
    j = wait(jid)
    res = {"job": {k: j.get(k) for k in ("id", "status", "error", "_elapsed_s")}}
    rs = runs_of(jid)
    if rs:
        res["file"] = check_file(rs[0], "i2v.mp4")
    return res


def cancel():
    jid = submit("sdcpp:wan2.2-ti2v-5b", "cancel probe: city at night, rain", {"width": 832, "height": 480, "frames": 49, "fps": 16, "steps": 20, "seed": 7})
    t0 = time.time()
    while time.time() - t0 < 120 and not sdcli_procs():
        time.sleep(2)
    engine_seen = bool(sdcli_procs())
    time.sleep(20)
    api("POST", f"/api/studio/jobs/{jid}/cancel", {})
    j = wait(jid)
    time.sleep(10)
    return {"engine_seen_before_cancel": engine_seen, "status_after_cancel": j.get("status"),
            "runs_after_cancel": len(runs_of(jid)), "sd_cli_left": sdcli_procs()}


def backend_pids():
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ? { $_.CommandLine -match 'fa7ceae1' -and $_.CommandLine -match '-m bcc' } | % ProcessId"],
                       capture_output=True, text=True)
    return [int(x) for x in r.stdout.split()]


def restart():
    """Hard-kill the backend mid-generation, start it again: expect interrupted_unknown, no orphan sd-cli, no silent resubmit."""
    jid = submit("sdcpp:wan2.2-ti2v-5b", "restart probe: waves on a beach", {"width": 832, "height": 480, "frames": 49, "fps": 16, "steps": 20, "seed": 7})
    while not sdcli_procs():
        time.sleep(2)
    time.sleep(20)
    engine_before = sdcli_procs()
    pids = backend_pids()
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    log("killed backend", pids, "engine before", engine_before)
    time.sleep(5)
    engine_after_kill = sdcli_procs()
    subprocess.Popen(["cmd.exe", "/c", str(HERE / "run-server.cmd")], creationflags=0x08000000)
    while True:
        try:
            api("GET", "/api/studio/models")
            break
        except Exception:
            time.sleep(3)
    time.sleep(15)
    j = api("GET", f"/api/studio/jobs/{jid}")
    return {"engine_before_kill": engine_before, "engine_alive_right_after_kill": engine_after_kill,
            "engine_after_restart": sdcli_procs(), "job_status_after_restart": j.get("status"),
            "job_error": j.get("error"), "runs": len(runs_of(jid)), "jobs_total": api("GET", "/api/studio/jobs").get("total")}


if __name__ == "__main__":
    for name, fn in (("image", image), ("t2v", t2v), ("i2v", i2v), ("cancel", cancel), ("restart", restart)):
        if len(sys.argv) > 1 and name not in sys.argv[1:]:
            continue
        step(name, fn)
    log("DONE")
