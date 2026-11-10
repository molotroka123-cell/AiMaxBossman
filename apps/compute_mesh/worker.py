"""Compute mesh worker: runs on the SECOND PC of the owner, started from the flash drive. Standard library only.

    python worker.py            (reads mesh.json next to this file: coordinator URL + pairing token)

What it does: registers with the coordinator, then polls for jobs and runs ONLY these two kinds:
  ping             -> returns hostname and time
  ollama_generate  -> sends the prompt to the Ollama on THIS machine (127.0.0.1) and returns the text
Anything else is refused. It opens no port, runs no shell command, reads no files except mesh.json, and stops when
the window is closed. The first start asks the person at the keyboard to agree.
"""
from __future__ import annotations

import json
import os
import platform
import sys
import time
import urllib.request
from pathlib import Path

OLLAMA = "http://127.0.0.1:11434"


def http(url: str, body: dict | None, token: str | None = None, timeout: int = 600) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read() or b"{}")


def local_models() -> list[str]:
    try:
        return [m["name"] for m in http(OLLAMA + "/api/tags", None, timeout=5).get("models", [])]
    except Exception:
        return []


def run_job(job: dict) -> tuple[bool, object]:
    typ, payload = job.get("type"), job.get("payload") or {}
    if typ == "ping":
        return True, {"hostname": platform.node(), "time": time.time()}
    if typ == "ollama_generate":
        model, prompt = payload.get("model"), payload.get("prompt")
        if not isinstance(model, str) or not isinstance(prompt, str):
            return False, "model and prompt are required"
        try:
            body = {"model": model, "prompt": prompt, "stream": False, "options": payload.get("options") or {}}
            out = http(OLLAMA + "/api/generate", body)
            return True, {"response": out.get("response", ""), "eval_count": out.get("eval_count")}
        except Exception as e:
            return False, ("local model failed: " + repr(e))[:300]
    return False, "job type " + repr(typ) + " is not allowed on this worker"


def loop(cfg: dict, rounds: int | None = None, pause: float = 2.0) -> None:
    base, token = cfg["coordinator"].rstrip("/"), cfg["token"]
    models = local_models()
    info = {"worker_id": cfg.get("worker_id"), "hostname": platform.node(), "cpu_count": os.cpu_count(), "ram_gb": None,
            "models": models}
    wid = http(base + "/register", info, token)["worker_id"]
    print("joined the mesh as", wid, "| models here:", len(models), "| close this window to stop")
    n = 0
    while rounds is None or n < rounds:
        n += 1
        job = http(base + "/take", {"worker_id": wid}, token).get("job")
        if not job:
            time.sleep(pause)
            continue
        ok, result = run_job(job)
        http(base + "/finish", {"worker_id": wid, "job_id": job["id"], "ok": ok, "result": result}, token)
        print("job", job["id"][:8], job["type"], "done" if ok else "failed")


def main() -> int:
    cfg_path = Path(__file__).resolve().parent / "mesh.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    consent = cfg_path.with_name("consent_" + platform.node() + ".txt")
    if not consent.exists():
        print("This will let the Bossman at", cfg["coordinator"], "run AI model jobs on THIS computer:", platform.node())
        if input("Type YES to allow: ").strip() != "YES":
            print("not allowed; nothing was started")
            return 1
        consent.write_text("allowed " + time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    loop(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
