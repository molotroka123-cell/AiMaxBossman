"""LIVE proof: mobile pult :8840, Chat tab, AGENT mode -> local model edits Bossman, runs tests, commits.

Exactly the phone's calls (app.js ask()): POST /m/api/threads -> PATCH /m/api/agent/sessions/{id} {mode:AGENT}
-> POST /m/api/agent/sessions/{id}/messages {mode:AGENT} -> poll /m/api/tasks/{id} -> POST /m/api/approvals/{id}.
Every approval is shown, checked against a scope (this task, terminal.run, cwd == proof worktree, no push/destructive
command) and only then approved; anything outside the scope is rejected. Raw log -> run_log.json.
"""
import json, re, sys, time
from datetime import datetime, timezone
from pult import client, panel_headers

WT = r"C:\Users\asd\Bossman\wt-agent-proof-20261025"
AGENT_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 41
DENY = re.compile(r"\bpush\b|reset\s+--hard|\brm\b|\bdel\b|\brmdir\b|--force|\bformat\b|clean\s+-", re.I)

INSTRUCTION = f"""Владелец: перепиши Bossman так, как я скажу.
Репозиторий Bossman: {WT} (git, ветка proof/pult-agent-self-edit-20261025).
Каждый вызов terminal.run делай с mode="project_host" и cwd="{WT}". Оболочка — cmd.exe (не bash).

Что сделать:
1) Создай новый файл apps/bossman-mobile/bossmobile/modes.py с функцией agent_mode_label(mode) -> str.
   Она возвращает русскую подпись чипа режима пульта: CHAT -> "Чат", AGENT -> "Агент", AUTO -> "Авто",
   CODE -> "Код", COMPUTER -> "Компьютер", SELF-IMPROVE -> "Самоулучшение".
   Регистр и пробелы по краям не важны; None, пустой или неизвестный режим -> "Чат".
2) Создай тест apps/bossman-mobile/tests/test_modes.py (pytest): все 6 режимов, " agent " в нижнем регистре с пробелами,
   None и неизвестный режим. Импорт: from bossmobile.modes import agent_mode_label (conftest уже кладёт пакет в sys.path).
3) Запусти python -m pytest apps/bossman-mobile/tests/test_modes.py -q -p no:cacheprovider и добейся, чтобы всё прошло.
4) Закоммить ровно эти два файла:
   git add apps/bossman-mobile/bossmobile/modes.py apps/bossman-mobile/tests/test_modes.py && git commit -m "feat(mobile): agent_mode_label - Russian labels for pult agent modes (owner instruction via pult AGENT mode)"
КАК ПИСАТЬ ФАЙЛЫ (cmd.exe ломает кириллицу и многострочные команды):
   - команда целиком только из ASCII-символов; кириллицу в коде пиши как \\uXXXX (например "\\u0427\\u0430\\u0442" = "Чат");
   - один файл = одна команда вида: python -c "open(r'ПУТЬ','w',encoding='utf-8').write('строка1\\nстрока2\\n')"
     (внутри write только одинарные кавычки, переводы строк как \\n, без with/;/powershell/echo);
   - после записи проверь файл: python -c "print(open(r'ПУТЬ',encoding='utf-8').read())".
Ничего не пушь.
В конце ответь: хеш коммита (git log -1 --format=%H) и итог pytest."""


def now():
    return datetime.now(timezone.utc).isoformat()


def main():
    c = client()
    log = {"started_at": now(), "agent_id": AGENT_ID, "worktree": WT, "instruction": INSTRUCTION,
           "calls": [], "approvals": [], "polls": []}

    def rec(method, path, body=None, **kw):
        t0 = time.perf_counter()
        r = c.request(method, path, json=body, **kw)
        try:
            j = r.json()
        except Exception:
            j = {"_text": r.text[:500]}
        log["calls"].append({"at": now(), "method": method, "path": path, "status": r.status_code,
                             "ms": round((time.perf_counter() - t0) * 1000), "body_keys": sorted(body) if isinstance(body, dict) else None})
        return r.status_code, j

    import os
    if os.environ.get("PROOF_THREAD"):
        sid = os.environ["PROOF_THREAD"]
        log["instruction"] = os.environ["PROOF_TEXT"]
    else:
        st, th = rec("POST", "/m/api/threads", {"title": "PROOF agent-mode self-edit 25.10"})
        assert st in (200, 201), th
        sid = th["id"]
    log["thread_id"] = sid
    st, p = rec("PATCH", f"/m/api/agent/sessions/{sid}", {"mode": "AGENT", "bypass_permissions": False})
    log["patch"] = {"status": st, "mode": p.get("mode") if isinstance(p, dict) else None,
                    "bypass_permissions": p.get("bypass_permissions") if isinstance(p, dict) else None}
    crid = "m-proof-" + datetime.now().strftime("%H%M%S")
    st, sent = rec("POST", f"/m/api/agent/sessions/{sid}/messages",
                   {"text": log["instruction"], "agent_id": AGENT_ID, "client_request_id": crid, "mode": "AGENT"})
    print("send", st, json.dumps({k: sent.get(k) for k in ("mode", "bypass_permissions", "task_id", "admission", "model", "agent")}, ensure_ascii=False)[:800])
    log["send"] = {"status": st, **{k: sent.get(k) for k in ("mode", "bypass_permissions", "task_id", "admission", "model", "agent", "route", "perf_trace_id")}}
    assert st == 200 and sent.get("task_id"), sent
    tid = int(sent["task_id"])
    log["task_id"] = tid
    seen = set()
    deadline = time.time() + 45 * 60
    last = None
    while time.time() < deadline:
        time.sleep(3)
        try:
            t = c.get(f"/m/api/tasks/{tid}").json()
        except Exception as exc:
            print("poll err", exc); continue
        s = t.get("status")
        if s != last:
            print(now(), "task", tid, s); last = s
            log["polls"].append({"at": now(), "status": s})
        if s == "waiting_approval":
            items = c.get("/m/api/approvals").json().get("items", [])
            for ap in items:
                if ap.get("task_id") != tid or ap["id"] in seen:
                    continue
                seen.add(ap["id"])
                prev = str(ap.get("preview") or "")
                args = {}
                m = re.search(r"аргументы:\s*(\{.*?\n\})", prev, re.S)
                if m:
                    try:
                        args = json.loads(m.group(1))
                    except ValueError:
                        args = {}
                cwd = args.get("cwd")
                cmd = str(args.get("command") or "")
                is_term = "terminal.run" in prev
                ok = (is_term and isinstance(cwd, str) and cwd.rstrip("\\/").lower() == WT.lower()
                      and args.get("mode") == "project_host" and bool(cmd) and not DENY.search(cmd))
                print(f"\n=== approval #{ap['id']} (task {tid}) ===\n{prev[:1500]}\n=== decision: {'APPROVE' if ok else 'REJECT'} ===")
                st, d = rec("POST", f"/m/api/approvals/{ap['id']}", {"approve": ok})
                log["approvals"].append({"id": ap["id"], "at": now(), "preview": prev[:4000], "cwd": cwd, "command": cmd,
                                         "terminal": is_term, "decision": "approve" if ok else "reject",
                                         "http": st, "result_status": d.get("status") if isinstance(d, dict) else None})
        if s in ("completed", "failed", "stopped", "blocked", "cancelled"):
            break
    log["final_task"] = {k: t.get(k) for k in t if k not in ("prompt",)}
    try:
        full = c.get(f"/panel/api/tasks/{tid}").json()
        log["runs"] = full.get("runs")
        log["run_events"] = []
        for run in full.get("runs") or []:
            evs = c.get(f"/panel/api/runs/{run['id']}/events", params={"limit": 1000}).json()
            log["run_events"] += [{k: e.get(k) for k in ("id", "run_id", "ts", "level", "kind", "message", "data")} for e in evs
                                  if e.get("kind") != "memory.recalled"]
        log["result_text"] = str(full.get("result") or "")[:3000]
        log["task_status"] = (full.get("task") or {}).get("status")
    except Exception as exc:
        log["tool_calls_error"] = repr(exc)
    log["finished_at"] = now()
    with open(os.environ.get("PROOF_LOG","run_log.json"), "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=1, default=str)
    print("\nFINAL", log.get("task_status"), "approvals:", [(a["id"], a["decision"]) for a in log["approvals"]])
    print("RESULT", log.get("result_text", "")[:1500])


if __name__ == "__main__":
    main()
