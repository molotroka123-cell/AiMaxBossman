"""Owner master prompt 01.11 item 1: prove readiness via /health + a real browser context + a real local model request."""
import json, sys, time
sys.path.insert(0, r"C:\Users\asd\Bossman\wt-runtime-20261101\tools")
from prove_chat_attachments import api

out = {"at": time.strftime("%Y-%m-%dT%H:%M:%S")}
live = api("GET", "/health/live")
out["runtime_sha"] = live.get("build_sha")
out["source_identity"] = live.get("source_identity")

# browser: a real headless Chromium context on a local page, then stop it
t0 = time.time()
sess = api("POST", "/api/browser/sessions", {"policy": {"allow_domains": ["example.com"]}})
sid = sess.get("session_id")
act = api("POST", f"/api/browser/sessions/{sid}/act", {"action": "navigate", "url": "https://example.com/"})
state = api("GET", f"/api/browser/sessions/{sid}/state")
health_mid = api("GET", "/health")
out["browser"] = {"session": sid, "seconds": round(time.time() - t0, 2), "url": state.get("url"),
                  "title_or_text": str(state.get("title") or state.get("text") or "")[:80],
                  "health_browser_while_open": health_mid["components"]["browser"]["status"]}
api("POST", f"/api/browser/sessions/{sid}/stop", {})

# a real local model request through the task path, timed
t0 = time.time()
task = api("POST", "/api/tasks", {"prompt": "Сколько будет 19*7? Ответь только числом.", "title": "readiness probe 01.11",
                                  "agent_id": int(sys.argv[1]) if len(sys.argv) > 1 else 52, "run_now": True})["task"]
while True:
    t = api("GET", f"/api/tasks/{task['id']}")
    tk = t.get("task", t)
    if tk["status"] in ("completed", "failed", "stopped", "blocked", "waiting_approval"):
        break
    if time.time() - t0 > 900:
        break
    time.sleep(2)
runs = t.get("runs") or []
res = (runs[-1].get("result") if runs else None) or tk.get("result")
out["local_request"] = {"task_id": task["id"], "status": tk["status"], "seconds": round(time.time() - t0, 1),
                        "answer": str(res)[:60], "correct": "133" in str(res),
                        "model": runs[-1].get("model_alias") if runs else None}
h = api("GET", "/health")
out["health"] = {"status": h["status"], "ready": h["ready"],
                 "not_healthy": {k: v["status"] for k, v in h["components"].items() if v["status"] not in ("HEALTHY", "ALIVE")}}
print(json.dumps(out, ensure_ascii=False, indent=1))
