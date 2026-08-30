"""RC TEST C — LIVE интеграционный прогон Command Center (Control Plane)."""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8800"
DATA_DIR = Path(r"C:\Users\timur\AppData\Local\Temp\opencode\rc_test_c\bcc-data")
MOCK_URL = "http://127.0.0.1:8899/v1"
HERE = Path(__file__).parent

rows: list[dict] = []


def record(action, endpoint, status, result, expected=None):
    if expected is not None:
        ok = status == expected
    elif status == "SKIP":
        ok = True
    else:
        ok = 200 <= status < 300
    rows.append({"action": action, "endpoint": endpoint, "http": status,
                 "result": result, "pass": bool(ok)})
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {action:52s} {endpoint:44s} HTTP {status}  {result}")


def main():
    token = (DATA_DIR / "token").read_text(encoding="utf-8").strip()
    client = httpx.Client(base_url=BASE, timeout=30.0)

    # ---------- LOGIN (как браузер: token → cookie + CSRF) ----------
    r = client.post("/api/login", json={"token": token, "label": "rc-live"})
    record("Login (UI entry)", "POST /api/login", r.status_code, "session+csrf")
    csrf = r.json()["csrf"]
    client.headers["X-BCC-CSRF"] = csrf
    client.headers["Content-Type"] = "application/json"

    # ================= 1. DASHBOARD SWEEP =================
    print("\n--- DASHBOARD SWEEP ---")
    gets = [
        ("Home: system", "/api/system"),
        ("Home: missions", "/api/missions"),
        ("Home: apps", "/api/apps"),
        ("Home: resources", "/api/resources"),
        ("Home: agentmap", "/api/agentmap"),
        ("Models: kinds", "/api/providers/kinds"),
        ("Models: providers", "/api/providers"),
        ("Models: list", "/api/models"),
        ("Agents: list", "/api/agents"),
        ("Tasks: list", "/api/tasks"),
        ("Tasks: queued", "/api/tasks?status=queued,running,waiting_approval"),
        ("Schedules: list", "/api/schedules"),
        ("Approvals: pending", "/api/approvals?status=pending"),
        ("Approvals: all", "/api/approvals?status=all"),
        ("System: activity", "/api/activity"),
        ("Browser: sessions", "/api/browser/sessions"),
        ("Terminal: roots", "/api/terminal/roots"),
        ("Terminal: sessions", "/api/terminal/sessions"),
        ("Missions: list", "/api/missions"),
        ("Governor: rules", "/api/governor/rules"),
        ("Governor: interventions", "/api/governor/interventions?limit=100"),
        ("Router: rules", "/api/router/rules"),
        ("Healing: rules", "/api/healing/rules"),
        ("Skills: list", "/api/skills"),
        ("Skills: mcp servers", "/api/mcp/servers"),
        ("Skills: mcp tools", "/api/mcp/tools"),
        ("Benchmarks: list", "/api/benchmarks"),
        ("Overview: agentmap", "/api/agentmap"),
        ("Overview: resources", "/api/resources"),
        ("OpenRouter: health", "/api/opencode/health"),
    ]
    for action, path in gets:
        try:
            r = client.get(path)
            record(action, f"GET {path}", r.status_code, "2xx")
        except Exception as exc:
            record(action, f"GET {path}", 0, f"exc {exc}")

    # Resources page: POST /api/resources/policy (read-modify-write текущего policy)
    r = client.get("/api/resources")
    cur_policy = (r.json() or {}).get("policy", "balanced")
    r = client.post("/api/resources/policy", json={"policy": cur_policy})
    record("Resources: policy write", "POST /api/resources/policy", r.status_code, "2xx")

    # Skills page: POST /api/mcp/policy — применимо только при наличии MCP-инструментов
    r = client.get("/api/mcp/tools")
    mcp_tools = r.json() if isinstance(r.json(), list) else []
    if mcp_tools:
        r = client.post("/api/mcp/policy", json={
            "canonical": mcp_tools[0].get("canonical"), "policy": "ask"})
        record("Skills: mcp policy write", "POST /api/mcp/policy", r.status_code, "2xx")
    else:
        record("Skills: mcp policy write", "POST /api/mcp/policy", "SKIP",
               "нет MCP-инструментов (UI показывает пустой список)")

    # Models page: discover (read-only)
    r = client.post("/api/models/discover", json={"extra_urls": []})
    record("Models: discover", "POST /api/models/discover", r.status_code, "2xx")

    # Agents page: create + patch
    r = client.post("/api/agents", json={
        "name": "rc_agent", "system_prompt": "RC test agent", "enabled": True,
        "tools": ["memory.fact.add"], "permissions": {}})
    record("Agents: create", "POST /api/agents", r.status_code,
           f"id={r.json().get('id')}" if r.status_code < 300 else r.text[:120])
    agent_id = r.json().get("id")

    # Tasks page: draft create + actions
    r = client.post("/api/tasks", json={"prompt": "rc draft task", "title": "rc_draft",
                                        "run_now": False})
    record("Tasks: create draft", "POST /api/tasks", r.status_code,
           f"task={r.json().get('task', {}).get('id')}" if r.status_code < 300 else r.text[:120])
    draft_id = r.json().get("task", {}).get("id")
    r = client.post(f"/api/tasks/{draft_id}/pause")
    record("Tasks: pause", f"POST /api/tasks/{draft_id}/pause", r.status_code, "2xx")
    r = client.post(f"/api/tasks/{draft_id}/resume")
    record("Tasks: resume", f"POST /api/tasks/{draft_id}/resume", r.status_code, "2xx")
    r = client.post(f"/api/tasks/{draft_id}/stop")
    record("Tasks: stop", f"POST /api/tasks/{draft_id}/stop", r.status_code, "2xx")

    # Schedules page: create/patch/delete
    r = client.post("/api/schedules", json={
        "name": "rc_schedule", "kind": "interval", "interval_minutes": 60, "enabled": True,
        "task_template": {"title": "RC scheduled job", "prompt": "rc prompt", "priority": 5}})
    record("Schedules: create", "POST /api/schedules", r.status_code,
           f"id={r.json().get('id')}" if r.status_code < 300 else r.text[:200])
    sched_id = r.json().get("id")
    r = client.patch(f"/api/schedules/{sched_id}", json={"enabled": False})
    record("Schedules: patch", f"PATCH /api/schedules/{sched_id}", r.status_code, "2xx")
    r = client.delete(f"/api/schedules/{sched_id}")
    record("Schedules: delete", f"DELETE /api/schedules/{sched_id}", r.status_code, "2xx")

    # Approvals page: manual create + decide
    r = client.post("/api/approvals", json={"kind": "manual", "preview": "rc manual approval"})
    record("Approvals: create", "POST /api/approvals", r.status_code,
           f"id={r.json().get('id')}" if r.status_code < 300 else r.text[:120])
    man_appr = r.json().get("id")
    r = client.post(f"/api/approvals/{man_appr}", json={"approve": True, "by": "rc-live"})
    record("Approvals: decide", f"POST /api/approvals/{man_appr}", r.status_code, "2xx")

    # System page: system + activity уже выше; settings не трогаем
    r = client.post("/api/missions", json={"title": "rc_mission", "goal": "rc acceptance goal"})
    record("Missions: create", "POST /api/missions", r.status_code,
           f"id={r.json().get('id')}" if r.status_code < 300 else r.text[:200])
    mission_id = r.json().get("id")

    # Governor: read + write-back same rules
    r = client.get("/api/governor/rules")
    rules = r.json()
    r = client.patch("/api/governor/rules", json=rules)
    record("Governor: rules patch", "PATCH /api/governor/rules", r.status_code, "2xx")

    # Router page: preview
    r = client.post("/api/router/preview", json={"prompt": "rc router preview"})
    record("Router: preview", "POST /api/router/preview", r.status_code, "2xx")

    # Terminal page: preview (AUTO), run AUTO, run ASK→approval→approved run
    r = client.post("/api/terminal/preview", json={"command": "dir", "mode": "project_host"})
    record("Terminal: preview", "POST /api/terminal/preview", r.status_code,
           f"decision={r.json().get('decision')}" if r.status_code < 300 else r.text[:120])
    r = client.post("/api/terminal/run", json={
        "command": "echo rc-terminal-ok", "mode": "project_host"})
    if r.status_code == 202:
        err_body = (r.json() or {}).get("error") or {}
        appr_id = err_body.get("approval_id")
        record("Terminal: run (ASK)", "POST /api/terminal/run", r.status_code,
               f"approval_id={appr_id}")
        r2 = client.post(f"/api/approvals/{appr_id}", json={"approve": True, "by": "rc-live"})
        record("Terminal: approve", f"POST /api/approvals/{appr_id}", r2.status_code, "2xx")
        r3 = client.post("/api/terminal/run", json={
            "command": "echo rc-terminal-ok", "mode": "project_host", "approved": True})
        record("Terminal: run approved", "POST /api/terminal/run", r3.status_code,
               f"session={r3.json().get('session_id')}" if r3.status_code < 300 else r3.text[:120])
        term_session = r3.json().get("session_id")
        time.sleep(1.5)
        r4 = client.get(f"/api/terminal/sessions/{term_session}")
        out_text = " ".join(str(x) for x in ((r4.json() or {}).get("output") or []))
        record("Terminal: status", f"GET /api/terminal/sessions/{term_session}",
               r4.status_code,
               f"exit={r4.json().get('exit_code')}, echo_ok={'rc-terminal-ok' in out_text}")
    else:
        record("Terminal: run (AUTO)", "POST /api/terminal/run", r.status_code,
               f"session={r.json().get('session_id')}" if r.status_code < 300 else r.text[:120])

    # Browser page: POST session — честный 503 без Playwright/Chromium, 2xx если есть
    r = client.post("/api/browser/sessions", json={"start_url": "http://example.com"})
    if r.status_code == 503:
        record("Browser: session (no Playwright)", "POST /api/browser/sessions",
               r.status_code, "EXPECTED 503 honest degrade", expected=503)
    else:
        record("Browser: session", "POST /api/browser/sessions", r.status_code, "2xx")
    # Browser page: POST /api/browser/sessions → 202 ask → approve → close
    r = client.post("/api/terminal/preview", json={"command": "type nul", "mode": "project_host"})
    record("Terminal: preview type", "POST /api/terminal/preview", r.status_code,
           f"decision={r.json().get('decision')}")

    # ================= 2. SCHEDULE_CREATE (известный баг) =================
    print("\n--- SCHEDULE_CREATE (баг title→name) ---")
    # ТОЧНО тот payload, что шлёт UI (ui/pages.js openScheduleModal, «Создать»)
    ui_payload = {
        "name": "rc_ui_daily", "kind": "daily", "enabled": True,
        "task_template": {"title": "RC UI scheduled", "prompt": "rc daily prompt",
                          "agent_id": agent_id, "priority": 5},
        "daily_time": "09:00",
    }
    r = client.post("/api/schedules", json=ui_payload)
    schedule_create_ok = 200 <= r.status_code < 300
    record("Tasks→По расписанию→Создать", "POST /api/schedules", r.status_code,
           "2xx (bug fixed)" if schedule_create_ok else f"BODY={r.text[:200]}")
    ui_sched_id = r.json().get("id") if schedule_create_ok else None

    # Регрессия: legacy payload с title вместо name ДОЛЖЕН остаться 422
    r = client.post("/api/schedules", json={
        "title": "legacy", "kind": "daily", "daily_time": "09:00",
        "task_template": {"prompt": "x"}})
    record("Legacy payload (title) → 422", "POST /api/schedules", r.status_code,
           "contract enforced (API требует name)", expected=422)

    # ================= 3. APPROVAL LIVE =================
    print("\n--- APPROVAL LIVE (task → approval → approve → execution) ---")
    r = client.post("/api/providers", json={
        "name": "rc_mock", "kind": "openai_compat", "base_url": MOCK_URL, "api_key": "rc-test"})
    record("Setup: provider mock", "POST /api/providers", r.status_code,
           f"id={r.json().get('id')}" if r.status_code < 300 else r.text[:200])
    provider_id = r.json().get("id")
    r = client.post("/api/models", json={
        "provider_id": provider_id, "name": "mock-llm", "alias": "rc-mock"})
    record("Setup: model", "POST /api/models", r.status_code,
           f"id={r.json().get('id')}" if r.status_code < 300 else r.text[:200])
    model_id = r.json().get("id")
    r = client.post(f"/api/models/{model_id}/check")
    record("Setup: model check", f"POST /api/models/{model_id}/check", r.status_code,
           f"status={r.json().get('status')}" if r.status_code < 300 else r.text[:120])

    r = client.post("/api/agents", json={
        "name": "rc_agent_llm", "system_prompt": "RC approval agent", "enabled": True,
        "model_id": model_id, "tools": ["memory.fact.add"], "permissions": {}})
    agent_llm_id = r.json().get("id")
    record("Setup: agent llm", "POST /api/agents", r.status_code, f"id={agent_llm_id}")

    def run_approval_flow(marker):
        r = client.post("/api/tasks", json={
            "prompt": f"Сохрани факт с маркером {marker}.", "title": f"rc_task {marker}",
            "agent_id": agent_llm_id, "run_now": True})
        assert r.status_code < 300, r.text
        task_id = r.json()["task"]["id"]
        status = None
        appr_id = None
        for _ in range(40):
            time.sleep(0.5)
            t = client.get(f"/api/tasks/{task_id}").json()
            task = t.get("task") or {}
            status = task.get("status")
            if status == "waiting_approval":
                pend = client.get("/api/approvals?status=pending").json()
                row = next((a for a in pend if a.get("task_id") == task_id), None)
                appr_id = row.get("id") if row else None
                break
        return task_id, status, appr_id

    def wait_task(task_id, statuses, timeout=40):
        deadline = time.time() + timeout
        while time.time() < deadline:
            t = client.get(f"/api/tasks/{task_id}").json()
            st = (t.get("task") or {}).get("status")
            if st in statuses:
                return t
            time.sleep(0.5)
        return t

    # --- APPROVE path ---
    task_id, status, appr_id = run_approval_flow("RCAPPROVE-A1")
    record("APPROVE: task → waiting_approval", f"GET /api/tasks/{task_id}",
           200 if status == "waiting_approval" else 500,
           f"status={status}, approval_id={appr_id}")
    r = client.post(f"/api/approvals/{appr_id}", json={"approve": True, "by": "rc-live"})
    record("APPROVE: decision", f"POST /api/approvals/{appr_id}", r.status_code,
           f"status={r.json().get('status')}" if r.status_code < 300 else r.text[:120])
    t = wait_task(task_id, ("completed", "failed", "stopped"))
    record("APPROVE: task executed", f"GET /api/tasks/{task_id}",
           200 if (t.get("task") or {}).get("status") == "completed" else 500,
           f"status={t.get('task', {}).get('status')}, result={str(t.get('result'))[:60]}")
    facts = client.get("/api/memory/facts", params={"query": "RCAPPROVE-A1"}).json()
    executed = facts.get("total", 0) > 0
    record("APPROVE: real execution (fact)", "GET /api/memory/facts?query=RCAPPROVE-A1",
           200 if executed else 500, f"fact stored={executed}")

    # --- REJECT path ---
    task_id, status, appr_id = run_approval_flow("RCREJECT-B2")
    record("REJECT: task → waiting_approval", f"GET /api/tasks/{task_id}",
           200 if status == "waiting_approval" else 500,
           f"status={status}, approval_id={appr_id}")
    r = client.post(f"/api/approvals/{appr_id}", json={"approve": False, "by": "rc-live"})
    record("REJECT: decision", f"POST /api/approvals/{appr_id}", r.status_code,
           f"status={r.json().get('status')}" if r.status_code < 300 else r.text[:120])
    t = wait_task(task_id, ("completed", "failed", "stopped"))
    facts = client.get("/api/memory/facts", params={"query": "RCREJECT-B2"}).json()
    not_executed = facts.get("total", 0) == 0
    record("REJECT: NO execution (no fact)", "GET /api/memory/facts?query=RCREJECT-B2",
           200 if not_executed else 500,
           f"fact stored={facts.get('total', 0) > 0} (must be False)")

    # cleanup mock provider chain
    client.delete(f"/api/models/{model_id}")
    client.delete(f"/api/providers/{provider_id}")

    # ================= итог =================
    out = HERE / "rc_results.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    failed = [r_ for r_ in rows if not r_["pass"]]
    print(f"\nTOTAL {len(rows)} | PASS {len(rows) - len(failed)} | FAIL {len(failed)}")
    if failed:
        print("FAILED:")
        for f in failed:
            print("  -", f["action"], f["endpoint"], f["http"], f["result"])
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
