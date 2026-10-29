"""Rehearse the owner's 5-10 minute Bossman demo against the live backend and write a receipt.

Every step goes through the same HTTP path the pult uses. Synthetic content only: a demo chat thread (archived
afterwards), a demo file in the PC-agent workspace, a demo picture. Nothing personal is read.

usage: python tools/demo_rehearsal.py [--skip-image] [--agent 41] [--out docs/audits/2026-10-29-demo-rehearsal]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prove_chat_attachments as p  # noqa: E402  (token + api helper shared with the other proofs)

STEPS: list[dict] = []


def step(name: str, fn):
    t0 = time.time()
    try:
        ok, detail = fn()
    except Exception as exc:  # a failed demo step is a finding, not a crash
        ok, detail = False, f"{type(exc).__name__}: {str(exc)[:300]}"
    STEPS.append({"step": name, "verdict": "PASS" if ok else "FAIL", "seconds": round(time.time() - t0, 1), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'} {name:28s} {STEPS[-1]['seconds']:7.1f}s  {str(detail)[:140]}", flush=True)
    return ok


def health():
    h = p.api("GET", "/health/live", timeout=15)
    return h.get("liveness") is True and h.get("source_identity") == "PASS", {"build": h.get("build_sha_short"), "version": h.get("release_version")}


def chat(agent: int, wait: int):
    th = p.api("POST", "/api/chat/threads", {"title": "DEMO rehearsal"})
    tid = th.get("id") or (th.get("thread") or {}).get("id")
    sent = p.api("POST", f"/api/chat/threads/{tid}/send", {"text": "Кто ты и что ты умеешь на этом ПК? Ответь двумя короткими предложениями.",
                                                            "agent_id": agent}, timeout=900)
    task_id = sent.get("task_id") or (sent.get("task") or {}).get("id") or (sent.get("turn") or {}).get("task_id")
    t0, status, answer = time.time(), "?", ""
    while time.time() - t0 < wait:
        turn = next((x for x in p.api("GET", f"/api/chat/threads/{tid}").get("turns") or []
                     if int(x.get("task_id") or 0) == int(task_id or 0)), {})
        status = turn.get("status")
        if status in ("completed", "failed", "stopped", "cancelled"):
            answer = str(turn.get("result") or turn.get("error") or "")
            break
        time.sleep(3)
    try:
        p.api("PATCH", f"/api/chat/threads/{tid}", {"archived": True})
    except Exception:
        pass
    return status == "completed" and len(answer) > 20, {"thread": tid, "task": task_id, "status": status, "answer": answer[:400]}


def pc_task():
    r = p.api("POST", "/api/pc-agent/tasks", {"prompt": "Создай файл demo-bossman.txt в рабочей папке с текстом: Bossman demo OK",
                                              "wait": True}, timeout=600)
    rec = r.get("task") if isinstance(r.get("task"), dict) else r
    tid = rec.get("task_id") or rec.get("id")
    approved = False
    if rec.get("status") == "waiting_approval":   # writing a file is an approval-gated step: the owner taps «Да» in the pult
        aid = (rec.get("pending_approval") or {}).get("approval_id")
        rec = p.api("POST", f"/api/pc-agent/tasks/{tid}/approve", {"approval_id": aid, "approve": True, "by": "owner",
                                                                    "wait": True, "wait_timeout_s": 60}, timeout=120)
        approved = True
        t0 = time.time()
        while rec.get("status") not in ("completed", "done", "succeeded", "failed", "stopped") and time.time() - t0 < 120:
            time.sleep(2)
            rec = p.api("GET", f"/api/pc-agent/tasks/{tid}", timeout=30)
            rec = rec.get("task") if isinstance(rec.get("task"), dict) else rec
    status = rec.get("status")
    # 29.10: the agent once reported «verified» while writing a placeholder — check the file independently via the harness
    path = next((s.get("path") for s in (rec.get("step_results") or []) if isinstance(s, dict) and s.get("path")), "")
    if status in ("completed", "done", "succeeded"):
        chk = p.api("POST", "/api/v20/harness/execute-step", {"step_id": f"demo-file-{tid}", "title": "Демо: файл агента",
                    "lesson": "Файл агента проверен по содержимому, а не по его отчёту", "topic": "demo",
                    "postcondition": {"type": "file_contains", "path": path, "text": "Bossman demo OK"}}, timeout=60)
        if not chk.get("ok"):
            return False, {"task_id": tid, "status": status, "content_check": chk.get("postcondition_detail"), "path": path}
    return status in ("completed", "done", "succeeded"), {"task_id": tid, "status": status, "owner_approved": approved,
                                                          "summary": str(rec.get("summary") or rec.get("result") or rec.get("error") or "")[:300]}


def stop_blocks_new_work():
    p.api("POST", "/api/pc-agent/stop", {"actor": "owner", "reason": "demo: STOP"}, timeout=30)
    blocked = False
    try:
        p.api("POST", "/api/pc-agent/tasks", {"prompt": "Покажи список файлов рабочей папки", "wait": False}, timeout=60)
    except Exception as exc:
        blocked = "409" in str(exc) or "PC_AGENT_STOPPED" in str(exc)
    p.api("POST", "/api/pc-agent/resume", {"actor": "owner"}, timeout=30)
    return blocked, {"new_task_blocked_while_stopped": blocked, "resumed": True}


def harness_receipt():
    here = Path(__file__).resolve()
    r = p.api("POST", "/api/v20/harness/execute-step", {"step_id": f"demo-{time.strftime('%Y%m%d-%H%M%S')}", "title": "Демо: шаг с квитанцией",
              "lesson": "Демо-прогон: шаг засчитан только после проверки постусловия", "topic": "demo",
              "postcondition": {"type": "file_contains", "path": str(here), "text": "def harness_receipt"}}, timeout=60)
    return bool(r.get("ok")) and len(str(r.get("sha256_receipt") or "")) == 64, {k: r.get(k) for k in ("postcondition_detail", "db_counts", "sha256_receipt")}


def image(wait: int):
    j = p.api("POST", "/api/direct-gen/jobs", {"model": "flux1-schnell", "prompt": "minimal poster, bold orange letter B on black, studio lighting",
                                              "resolution": "768x768", "steps": 4, "seed": 29}, timeout=120)
    jid = j.get("job_id") or (j.get("job") or {}).get("job_id")
    t0, st = time.time(), {}
    while time.time() - t0 < wait:
        st = next((x for x in p.api("GET", "/api/direct-gen/jobs", timeout=60).get("jobs") or [] if x.get("job_id") == jid), {})
        if st.get("status") in ("completed", "failed", "cancelled"):
            break
        time.sleep(4)
    res = st.get("result") or {}
    return st.get("status") == "completed" and bool(res.get("file")), {"job_id": jid, "status": st.get("status"), "sha256": res.get("sha256"),
                                                                       "seconds_to_done": round(time.time() - t0, 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", type=int, default=41)
    ap.add_argument("--wait", type=int, default=600)
    ap.add_argument("--skip-image", action="store_true")
    ap.add_argument("--out", default="docs/audits/2026-10-29-demo-rehearsal")
    a = ap.parse_args()
    step("1 health + build", health)
    step("2 chat (local model)", lambda: chat(a.agent, a.wait))
    step("3 PC agent task", pc_task)
    step("4 STOP blocks new work", stop_blocks_new_work)
    step("5 harness receipt", harness_receipt)
    if not a.skip_image:
        step("6 local image", lambda: image(a.wait))
    ok = all(s["verdict"] == "PASS" for s in STEPS)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rec = {"verdict": "PASS" if ok else "FAIL", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "steps": STEPS,
           "total_seconds": round(sum(s["seconds"] for s in STEPS), 1)}
    (out / f"receipt-{time.strftime('%Y%m%d-%H%M')}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print("VERDICT", rec["verdict"], "| total", rec["total_seconds"], "s")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
