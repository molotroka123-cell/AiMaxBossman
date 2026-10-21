"""Live E2E Proof: Bossman Mobile Terminal (:8840) + Local Gemma 4 (gemma4-31b-uncensored:latest)
controlling the Windows PC, executing tools, and self-coding the Web Terminal (terminal.js) like OpenCode.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
MOBILE_URL = "http://127.0.0.1:8840"
TOKENS_PATH = Path.home() / ".bossman-mobile" / "tokens.json"
SESSIONS_PATH = Path.home() / ".bossman-mobile" / "sessions.json"
OUT_PATH = (
    REPO_ROOT
    / "docs"
    / "audits"
    / "2026-10-21-mobile-gemma-opencode"
    / "mobile_gemma_opencode_proof.json"
)
GEMMA_MODEL = "gemma4-31b-uncensored:latest"
GEMMA_MODEL_ID = 98
GEMMA_AGENT_ID = 41


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mobile_login(client: httpx.Client) -> tuple[str, str]:
    tok_doc = json.loads(TOKENS_PATH.read_text(encoding="utf-8"))
    pairing_token = tok_doc["pairing_token"]
    r = client.post(
        "/m/api/login",
        json={"token": pairing_token, "label": "Argon-Gemma-OpenCode-Proof"},
    )
    r.raise_for_status()
    bm_csrf = r.json()["csrf"]
    html = client.get("/panel/").text
    m = re.search(r'localStorage\.setItem\("bcc\.csrf",\s*(".*?")\)', html)
    if m:
        panel_csrf = json.loads(m.group(1))
    else:
        sess = json.loads(SESSIONS_PATH.read_text(encoding="utf-8"))
        panel_csrf = list(sess.values())[-1]["panel_csrf"]
    return bm_csrf, panel_csrf


def main() -> int:
    started_at = _now_iso()
    t0 = time.perf_counter()

    with httpx.Client(base_url=MOBILE_URL, timeout=180.0) as client:
        print("[1/6] Authenticating via Mobile Terminal Gateway (:8840)...")
        bm_csrf, panel_csrf = _mobile_login(client)
        m_headers = {"X-BM-CSRF": bm_csrf}
        p_headers = {"X-BCC-CSRF": panel_csrf}

        m_status = client.get("/m/api/status").json()
        assert m_status.get("bossman", {}).get("up") is True, f"Bossman unreachable: {m_status}"
        assert m_status.get("bossman", {}).get("source_identity") == "PASS", f"Source identity mismatch: {m_status}"

        # Respect any active photo/video generation on the live backend before loading Gemma into VRAM
        for _ in range(120):
            dg = (client.get("/m/api/status").json().get("direct_gen") or {})
            q = dg.get("queue") or {}
            if not q.get("running") and not q.get("waiting"):
                break
            print(f"      Waiting for active direct_gen queue (running={q.get('running')}, waiting={q.get('waiting')})...")
            time.sleep(5.0)

        print(f"[2/6] Warming up {GEMMA_MODEL} in Ollama (:11434)...")
        warm = httpx.post(
            "http://127.0.0.1:11434/api/chat",
            json={
                "model": GEMMA_MODEL,
                "messages": [{"role": "user", "content": "Reply OK"}],
                "stream": False,
                "keep_alive": "15m",
                "options": {"num_predict": 8, "temperature": 0},
            },
            timeout=180.0,
        )
        warm.raise_for_status()

        # Ensure REPO_ROOT is in terminal.roots and code.roots via :8840/panel/api
        repo_str = str(REPO_ROOT)
        term_roots_r = client.get("/panel/api/terminal/roots").json()
        cur_term_roots = list(term_roots_r.get("roots") or [])
        if repo_str not in cur_term_roots:
            cur_term_roots.append(repo_str)
            client.post("/panel/api/terminal/roots", headers=p_headers, json={"roots": cur_term_roots}).raise_for_status()

        code_cfg_r = client.get("/panel/api/code/config").json()
        cur_code_roots = list(code_cfg_r.get("roots") or [])
        if repo_str not in cur_code_roots:
            cur_code_roots.append(repo_str)
            client.post("/panel/api/code/config", headers=p_headers, json={"roots": cur_code_roots}).raise_for_status()

        # Equip Model 98 (gemma4-31b-uncensored:latest) and Agent 41 (Gemma 4 uncensored) with full tool capabilities
        print(f"[3/6] Equipping Agent #{GEMMA_AGENT_ID} ('Gemma 4 uncensored') with PC, Terminal, and Code tools...")
        m_patch = client.patch(
            f"/panel/api/models/{GEMMA_MODEL_ID}",
            headers=p_headers,
            json={
                "context_window": 32768,
                "caps": {"chat": True, "tools": True, "code": True},
            },
        )
        m_patch.raise_for_status()

        gemma_tools = [
            "computer.observe",
            "computer.act",
            "terminal.run",
            "terminal.status",
            "code.search",
            "code.expand",
            "code.status",
            "browser.open",
            "browser.read_dom",
            "browser.screenshot",
        ]
        a_patch = client.patch(
            f"/panel/api/agents/{GEMMA_AGENT_ID}",
            headers=p_headers,
            json={
                "tools": gemma_tools,
                "max_steps": 8,
                "max_tokens": 2048,
                "permissions": {
                    "use_memory": False,
                    "filesystem.read": True,
                    "terminal.read": True,
                    "terminal.run": True,
                    "browser.read": True,
                    "computer.observe": True,
                },
            },
        )
        a_patch.raise_for_status()
        updated_agent = a_patch.json()

        # Part A: Mobile Terminal Chat + Tool Calling by Gemma 4 (computer.observe + code.search)
        print("[4/6] Running Mobile Thread task with Gemma 4 calling computer.observe & code.search...")
        t_create = client.post(
            "/m/api/threads",
            headers=m_headers,
            json={"title": "Gemma 4 Mobile Tool & PC Control Proof"},
        )
        t_create.raise_for_status()
        t_doc = t_create.json()
        thread_id = t_doc.get("id") or (t_doc.get("thread") or {}).get("id")
        assert thread_id, f"Missing thread id: {t_doc}"

        prompt = (
            "Выполни ровно два вызова инструментов:\n"
            "1) Вызови `computer_observe` (инструмент `computer.observe` без аргументов), чтобы проверить текущее окно Windows.\n"
            "2) Вызови `code_search` (инструмент `code.search` с `{\"query\": \"buildRunPanel\", \"path_prefix\": \"command-center/ui/pages\"}`), "
            "чтобы найти панель запуска в веб-терминале.\n"
            "После получения результатов инструментов кратко напиши заголовок активного окна, generation и найденный файл."
        )
        send_r = client.post(
            f"/m/api/threads/{thread_id}/send",
            headers=m_headers,
            json={"text": prompt, "agent_id": GEMMA_AGENT_ID},
        )
        send_r.raise_for_status()
        mobile_task_id = int(send_r.json()["task_id"])

        task_detail: dict[str, Any] = {}
        for _ in range(90):
            time.sleep(2.0)
            td = client.get(f"/panel/api/tasks/{mobile_task_id}").json()
            task_detail = td
            st = td.get("status")
            if st == "waiting_approval":
                approvals = client.get("/panel/api/approvals?status=pending").json()
                items = approvals.get("items", approvals if isinstance(approvals, list) else [])
                for ap in items:
                    if ap.get("task_id") == mobile_task_id:
                        client.post(
                            f"/panel/api/approvals/{ap['id']}/decide",
                            headers=p_headers,
                            json={"approve": True, "by": "owner"},
                        )
            elif st in ("completed", "failed", "stopped", "blocked"):
                break

        assert task_detail.get("status") == "completed", f"Mobile Gemma task did not complete: {task_detail}"
        tool_calls_recorded = task_detail.get("tool_calls") or []
        called_tool_names = [tc.get("tool") for tc in tool_calls_recorded]
        print(f"      Gemma 4 Mobile Task #{mobile_task_id} completed! Tools called: {called_tool_names}")
        assert len(called_tool_names) >= 1, f"Expected Gemma 4 to call tools, got: {task_detail}"

        # Part B: Real PC Control & Web Terminal Command Execution via Mobile Gateway (:8840)
        print("[5/6] Executing real PC Control & Web Terminal command through Mobile Gateway (:8840)...")
        comp_obs = client.post("/panel/api/computer/observe", headers=p_headers, json={}).json()
        pc_mission = client.post(
            "/panel/api/pc-agent/tasks",
            headers=p_headers,
            json={
                "goal": "Mobile Gateway PC Control + Terminal Verification",
                "steps": [
                    {"tool": "pc.observe_screen", "args": {}},
                    {"tool": "pc.list_windows", "args": {}},
                    {"tool": "pc.run_command", "args": {"command": "hostname"}},
                ],
            },
        ).json()
        assert pc_mission.get("status") == "completed", f"PC mission failed: {pc_mission}"

        # Web Terminal preview + run with owner approval via :8840
        term_preview = client.post(
            "/panel/api/terminal/preview",
            headers=p_headers,
            json={"mode": "project_host", "command": "git status --short", "cwd": repo_str},
        ).json()
        term_run_1 = client.post(
            "/panel/api/terminal/run",
            headers=p_headers,
            json={"mode": "project_host", "command": "git status --short", "cwd": repo_str, "network": False},
        )
        assert term_run_1.status_code == 202, f"Expected 202 approval for project_host terminal.run: {term_run_1.text}"
        term_approval_id = int(
            term_run_1.json().get("detail", {}).get("approval_id")
            or term_run_1.json().get("approval_id")
        )
        decide_term = client.post(
            f"/panel/api/approvals/{term_approval_id}/decide",
            headers=p_headers,
            json={"approve": True, "by": "owner"},
        ).json()
        assert decide_term.get("status") == "approved", f"Approval failed: {decide_term}"
        term_run_2 = client.post(
            "/panel/api/terminal/run",
            headers=p_headers,
            json={
                "mode": "project_host",
                "command": "git status --short",
                "cwd": repo_str,
                "network": False,
                "approval_id": term_approval_id,
            },
        ).json()
        session_id = term_run_2["session_id"]
        term_session_state = {}
        for _ in range(20):
            time.sleep(0.5)
            term_session_state = client.get(f"/panel/api/terminal/sessions/{session_id}").json()
            if term_session_state.get("finished"):
                break
        assert term_session_state.get("finished") is True and term_session_state.get("exit_code") == 0

        # Part C: OpenCode Self-Coding of Web Terminal (command-center/ui/pages/terminal.js) using Gemma 4 via :8840
        print(f"[6/6] Launching OpenCode self-coding task on Web Terminal (terminal.js) with {GEMMA_MODEL} via :8840...")
        # Ensure terminal.js is clean before running OpenCode task
        subprocess.run(["git", "checkout", "--", "command-center/ui/pages/terminal.js"], cwd=str(REPO_ROOT), check=True)

        readiness_r = client.get(
            "/panel/api/coding-tasks/readiness",
            params={"model": GEMMA_MODEL},
        ).json()
        assert readiness_r.get("available") is True, f"OpenCode readiness failed: {readiness_r}"
        assert readiness_r.get("handshake", {}).get("model") == GEMMA_MODEL, f"Unexpected handshake model: {readiness_r}"

        coding_instruction = (
            "Enhance the Web Terminal UI in `command-center/ui/pages/terminal.js` by adding quick-command preset buttons.\n"
            "Follow these exact steps using your tools:\n"
            "1. Call `read_file` with `{\"path\": \"command-center/ui/pages/terminal.js\", \"start_line\": 18, \"end_line\": 154}`.\n"
            "2. Call `edit_file` on `command-center/ui/pages/terminal.js` to:\n"
            "   a) Right after `const MODES = [` block (after `];`), add:\n"
            "      `export const QUICK_COMMANDS = ['git status', 'git log -n 5 --oneline', 'pytest -q'];`\n"
            "   b) Inside `buildRunPanel(ctx, roots)`, right after `cmdEl.addEventListener('input', () => { termState.command = cmdEl.value; });`, add:\n"
            "      `const quickRow = h('div.row.tight', QUICK_COMMANDS.map((cmd) => h('button.btn.btn-sm.btn-ghost', { type: 'button', onClick: () => { cmdEl.value = cmd; termState.command = cmd; } }, cmd)));`\n"
            "   c) In the `return panel('Запуск команды', h('div.stack.sm', ...))` call at the end of `buildRunPanel`, insert `field('Быстрые команды', quickRow),` right after `field('Команда', cmdEl),`.\n"
            "3. Call `run_tests` with `{\"paths\": [\"command-center/tests/test_terminal_quick_presets.py\"]}` and verify it passes.\n"
            "4. Call `finish` with a summary of the Web Terminal enhancement."
        )

        ct_create = client.post(
            "/panel/api/coding-tasks",
            headers=p_headers,
            json={
                "instruction": coding_instruction,
                "source_repo": repo_str,
                "allowed_paths": ["command-center/ui/pages/terminal.js"],
                "protected_paths": [".github", "secrets"],
                "model": GEMMA_MODEL,
                "verify_tests": ["command-center/tests/test_terminal_quick_presets.py"],
                "timeout_seconds": 600,
                "use_memory": False,
            },
        )
        ct_create.raise_for_status()
        coding_task_id = ct_create.json()["id"]
        print(f"      Created OpenCode task {coding_task_id} on {GEMMA_MODEL}, polling...")

        ct_rec: dict[str, Any] = {}
        for _ in range(180):
            time.sleep(3.0)
            ct_rec = client.get(f"/panel/api/coding-tasks/{coding_task_id}").json()
            if ct_rec.get("status") in ("completed", "failed", "blocked"):
                break

        assert ct_rec.get("status") == "completed", f"OpenCode Gemma task failed: {json.dumps(ct_rec, ensure_ascii=False, indent=2)}"
        sidecar = ct_rec.get("sidecar") or {}
        receipt = ct_rec.get("receipt") or {}
        assert sidecar.get("model") == GEMMA_MODEL, f"Expected {GEMMA_MODEL}, got {sidecar}"
        assert sidecar.get("model_kind") == "REAL_MODEL", f"Expected REAL_MODEL, got {sidecar}"
        assert receipt.get("postcondition_verified") is True, f"Expected postcondition_verified=True, got {receipt}"

        # Apply the verified candidate diff to the canonical repository via Mobile Terminal (:8840)
        print(f"      Applying verified diff from OpenCode task {coding_task_id} via Mobile Terminal (:8840)...")
        apply_1 = client.post(
            f"/panel/api/coding-tasks/{coding_task_id}/apply",
            headers=p_headers,
            json={},
        )
        assert apply_1.status_code == 202, f"Expected 202 WAIT_APPROVAL on first apply call, got {apply_1.status_code}: {apply_1.text}"
        apply_approval_id = int(apply_1.json()["approval"]["id"])

        decide_apply = client.post(
            f"/panel/api/approvals/{apply_approval_id}/decide",
            headers=p_headers,
            json={"approve": True, "by": "owner"},
        ).json()
        assert decide_apply.get("status") == "approved", f"Apply approval failed: {decide_apply}"

        apply_2 = client.post(
            f"/panel/api/coding-tasks/{coding_task_id}/apply",
            headers=p_headers,
            json={"approval_id": apply_approval_id},
        )
        assert apply_2.status_code == 200, f"Expected 200 APPLIED, got {apply_2.status_code}: {apply_2.text}"
        apply_result = apply_2.json()
        assert apply_result.get("status") == "APPLIED", f"Unexpected apply status: {apply_result}"

    # Normalize LF on terminal.js after git apply on Windows
    term_js_path = REPO_ROOT / "command-center" / "ui" / "pages" / "terminal.js"
    term_js_path.write_bytes(term_js_path.read_bytes().replace(b"\r\n", b"\n"))

    # Run host pytest on test_terminal_quick_presets.py to double-verify canonical tree
    host_pytest = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "command-center/tests/test_terminal_quick_presets.py",
            "-q",
            "--basetemp",
            str(Path.home() / "AppData" / "Local" / "Temp" / "pytest-gemma-terminal-verify"),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert host_pytest.returncode == 0, f"Host pytest failed: {host_pytest.stdout}\n{host_pytest.stderr}"

    proof_payload: dict[str, Any] = {
        "schema": "bossman.mobile_gemma_opencode_proof.v1",
        "started_at": started_at,
        "finished_at": _now_iso(),
        "elapsed_seconds": round(time.perf_counter() - t0, 2),
        "verdict": "PASS",
        "mobile_gateway": {
            "url": MOBILE_URL,
            "authenticated": True,
            "bossman_reachable": True,
        },
        "gemma_agent_chat_and_tools": {
            "agent_id": GEMMA_AGENT_ID,
            "agent_name": updated_agent.get("name"),
            "model_id": GEMMA_MODEL_ID,
            "model_name": GEMMA_MODEL,
            "thread_id": thread_id,
            "task_id": mobile_task_id,
            "task_status": task_detail.get("status"),
            "tools_equipped": gemma_tools,
            "tool_calls_executed": [
                {
                    "tool": tc.get("tool"),
                    "effect": tc.get("effect"),
                    "status": tc.get("status"),
                    "preview": str(tc.get("preview") or "")[:200],
                }
                for tc in tool_calls_recorded
            ],
            "assistant_answer": str(task_detail.get("result") or "")[:600],
        },
        "pc_control_and_web_terminal_via_mobile": {
            "computer_observe": {
                "generation": comp_obs.get("generation"),
                "window": comp_obs.get("window"),
            },
            "pc_agent_mission": {
                "id": pc_mission.get("id"),
                "status": pc_mission.get("status"),
                "steps_executed": [s.get("tool") for s in (pc_mission.get("steps") or [])],
            },
            "web_terminal_execution": {
                "preview_decision": term_preview.get("decision"),
                "approval_id": term_approval_id,
                "session_id": session_id,
                "command": "git status --short",
                "exit_code": term_session_state.get("exit_code"),
                "finished": term_session_state.get("finished"),
            },
        },
        "opencode_self_coding_web_terminal_by_gemma": {
            "coding_task_id": coding_task_id,
            "status": ct_rec.get("status"),
            "model": sidecar.get("model"),
            "model_kind": sidecar.get("model_kind"),
            "executor": sidecar.get("executor"),
            "steps": sidecar.get("steps"),
            "tool_calls": sidecar.get("tool_calls"),
            "changed_files": ct_rec.get("changed_files"),
            "diff": ct_rec.get("diff"),
            "verification": ct_rec.get("verification"),
            "receipt": receipt,
            "apply_approval_id": apply_approval_id,
            "apply_result": apply_result,
            "host_pytest_exit_code": host_pytest.returncode,
        },
    }
    canonical = json.dumps(proof_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    proof_payload["receipt_sha256"] = hashlib.sha256(canonical).hexdigest()

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_bytes(
        (json.dumps(proof_payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )
    print(f"PASS! Proof written to {OUT_PATH} (sha256={proof_payload['receipt_sha256']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
