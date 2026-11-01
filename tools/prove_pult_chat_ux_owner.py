"""Owner 31.10 live proof through the REAL mobile pult (:8840 → :8801), as the owner (pairing login, cookie + CSRF).

«менять модели можно, они подхватывают контекст внутри чата; проекты; пару чатов одновременно; после compact можно
перенести данные чата в другой; GLM пишет файлы только внутри проекта». Synthetic messages only; prints ids/statuses,
never chat contents beyond the planted code word check.

    python -X utf8 tools/prove_pult_chat_ux_owner.py [--local 41] [--cloud 46]
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prove_auto_full_owner as owner  # noqa: E402  (owner login + pult http)

RECEIPT = Path(__file__).resolve().parents[1] / "docs" / "audits" / "2026-10-31-pult-chat-ux-owner" / "receipt.json"
DONE = {"completed", "failed", "stopped", "blocked", "waiting_approval", "cancelled"}


def call(h, method, path, body=None):
    code, data, _ = owner.http(method, path, body, h)
    return code, data


def turns(h, tid):
    code, data = call(h, "GET", f"/m/api/threads/{tid}")
    return (data.get("turns") or []) if code == 200 else []


def wait_turn(h, tid, task_id, minutes=12):
    if not task_id:                         # the send was refused: report it, do not wait for a task that does not exist
        return {"status": "not_sent", "task_id": None}
    end = time.time() + minutes * 60
    while time.time() < end:
        for t in turns(h, tid):
            if t.get("task_id") == task_id and t.get("status") in DONE:
                return t
        time.sleep(4)
    return {"status": "timeout", "task_id": task_id}


def send(h, tid, text, agent):
    code, data = call(h, "POST", f"/m/api/threads/{tid}/send",
                      {"text": text, "agent_id": agent, "client_request_id": "ux-" + secrets.token_hex(8)})
    task = (data.get("task") or {}).get("id") if isinstance(data, dict) else None
    return code, task, data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", type=int, default=41)
    ap.add_argument("--cloud", type=int, default=46)
    ap.add_argument("--receipt", default=str(RECEIPT))
    a = ap.parse_args()
    receipt = Path(a.receipt)
    h = owner.login()
    word = "ФИАЛКА-" + str(secrets.randbelow(900) + 100)
    checks: dict[str, dict] = {}

    # 1. project + chat in the project
    code, proj = call(h, "POST", "/m/api/projects", {"name": f"UX proof {time.strftime('%H%M%S')}"})
    slug = proj.get("slug")
    checks["project_created"] = {"ok": code == 201 and bool(slug), "code": code}
    code, th = call(h, "POST", "/m/api/threads", {"project": slug})
    a_id = th.get("id")
    checks["chat_in_project"] = {"ok": code == 201 and th.get("project") in (slug, proj.get("name")), "code": code}

    # 2. local model hears the code word; 3. the CLOUD model in the SAME chat recalls it (model switch keeps context)
    _, t1, _ = send(h, a_id, f"Запомни кодовое слово {word}. Ответь одним словом: ок.", a.local)
    r1 = wait_turn(h, a_id, t1)
    _, t2, _ = send(h, a_id, "Какое кодовое слово я назвал выше в этом чате? Ответь только им.", a.cloud)
    r2 = wait_turn(h, a_id, t2)
    checks["local_turn"] = {"ok": r1.get("status") == "completed", "task": t1, "status": r1.get("status")}
    checks["model_switch_keeps_context"] = {"ok": r2.get("status") == "completed" and word in str(r2.get("result") or ""),
                                            "task": t2, "status": r2.get("status")}

    # 4. cloud agent writes a file ONLY inside the project folder
    fname = f"hello_cloud_{secrets.token_hex(3)}.py"
    _, t3, _ = send(h, a_id, f"Создай в папке проекта файл {fname} с одной строкой print('cloud ok') через code.write.",
                    a.cloud)
    r3 = wait_turn(h, a_id, t3)
    code, files = call(h, "GET", f"/m/api/projects/{slug}/files")
    names = [f.get("name") for f in (files.get("entries") or [])] if isinstance(files, dict) else []
    checks["cloud_writes_in_project"] = {"ok": fname in names, "task": t3, "status": r3.get("status"), "files": len(names)}

    # 5. two chats at once (local + cloud), neither blocks the other
    _, b = call(h, "POST", "/m/api/threads", {})
    _, c = call(h, "POST", "/m/api/threads", {})
    res: dict[str, tuple] = {}

    def go(key, tid, agent):
        res[key] = send(h, tid, "Сколько будет 17*3? Ответь только числом.", agent)

    ths = [threading.Thread(target=go, args=("b", b["id"], a.cloud)), threading.Thread(target=go, args=("c", c["id"], a.local))]
    [t.start() for t in ths]
    [t.join() for t in ths]
    # queue a follow-up in B while it runs, and steer C
    qcode, _ = call(h, "POST", f"/m/api/threads/{b['id']}/queue", {"text": "А 18*3? Только число.", "agent_id": a.cloud})
    scode, _ = call(h, "POST", f"/m/api/threads/{c['id']}/steer", {"text": "Ответь коротко."})
    rb, rc = wait_turn(h, b["id"], res["b"][1]), wait_turn(h, c["id"], res["c"][1])
    checks["parallel_chats"] = {"ok": res["b"][0] < 300 and res["c"][0] < 300 and rb.get("status") == "completed"
                                and rc.get("status") == "completed" and "51" in str(rb.get("result")) and "51" in str(rc.get("result")),
                                "codes": [res["b"][0], res["c"][0]], "status": [rb.get("status"), rc.get("status")]}
    checks["queue_and_steer_accepted"] = {"ok": qcode == 201 and scode in (200, 201, 202), "codes": [qcode, scode]}
    end = time.time() + 600
    queued_done = False
    while time.time() < end and not queued_done:
        tb = turns(h, b["id"])
        queued_done = len(tb) >= 2 and tb[-1].get("status") in DONE
        time.sleep(5)
    tb = turns(h, b["id"])
    checks["queued_prompt_ran_after"] = {"ok": len(tb) >= 2 and "54" in str(tb[-1].get("result") or ""),
                                         "turns": len(tb), "status": tb[-1].get("status") if tb else None}

    # 6. compact chat A, transfer its context into a new chat, the next turn there knows the code word
    ccode, _ = call(h, "POST", f"/m/api/agent/sessions/{a_id}/compact", {})     # what the pult's /compact calls
    listed, end = False, time.time() + 900
    while not listed and time.time() < end:
        _, comp = call(h, "GET", "/m/api/compacted-threads")
        listed = a_id in json.dumps(comp)
        time.sleep(6)
    _, d = call(h, "POST", "/m/api/threads", {})
    icode, _ = call(h, "POST", f"/m/api/threads/{d['id']}/import-context", {"source_thread_id": a_id, "agent_id": a.cloud})
    _, t5, _ = send(h, d["id"], "Какое кодовое слово упоминалось в перенесённом контексте? Ответь только им.", a.cloud)
    r5 = wait_turn(h, d["id"], t5)
    checks["compact_transfer"] = {"ok": listed and icode in (200, 201) and word in str(r5.get("result") or ""),
                                  "compact_code": ccode, "listed": listed, "import_code": icode, "status": r5.get("status")}

    ok = all(v["ok"] for v in checks.values())
    receipt.parent.mkdir(parents=True, exist_ok=True)
    out = {"verdict": "PASS" if ok else "FAIL", "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "local_agent": a.local, "cloud_agent": a.cloud, "checks": checks}
    out["runtime_sha"] = (json.loads(__import__("urllib.request").request.urlopen(
        "http://127.0.0.1:8801/health/live", timeout=10).read()) or {}).get("build_sha")
    receipt.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for k, v in checks.items():
        print(("PASS " if v["ok"] else "FAIL ") + k, {x: y for x, y in v.items() if x != "ok"})
    print("VERDICT", out["verdict"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
