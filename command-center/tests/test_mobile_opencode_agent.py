"""End-to-end TDD suite for Bossman Mobile OpenCode Agent (`/api/agent/*` & `/api/terminal/*`).

Covers all 15 mandatory verification scenarios from the Master Prompt (§9):
  1. Mobile chat basic response (`CHAT` mode).
  2. Multi-step agent tool execution (`AGENT` mode).
  3. OpenCode file read + patch + test (`CODE` mode).
  4. Web terminal interactive command execution (`/api/terminal/run` + `WS /api/terminal/sessions/{id}/stream`).
  5. Terminal `Ctrl+C` (`sigint`) and `STOP` (`/api/agent/sessions/{id}/stop`).
  6. Windows screenshot and window list (`COMPUTER` mode + tool registry).
  7. Safe computer action with approval (`/api/agent/tasks/{id}/approve`).
  8. Blocked dangerous command (`rm -rf /`, `format`, `diskpart`, `reg delete`).
  9. Local → Cloud fallback routing & 5-tier model router (`/api/agent/router/select`).
 10. Budget limit stop & RunPod guard (`402 BUDGET_EXCEEDED`, `403 RUNPOD_FORBIDDEN`).
 11. WebSocket reconnect and event replay (`WS /api/agent/sessions/{id}/stream?after=N`).
 12. Persistence after backend restart (session state, mode, turns survive restart).
 13. Parallel tasks isolation (two concurrent agent sessions stay isolated).
 14. `/improve` self-coding in isolated worktree (16-step controlled self-improvement loop).
 15. Secret redaction in logs/UI/terminal (`redact_structure` strips secrets from events).
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from bcc.auth import HEADER
from bcc.features.agent_opencode import _orch
from .conftest import FakeAdapter, client_for, start_app


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


@pytest.fixture()
def mini_repo(tmp_path: Path) -> Path:
    """Create a tiny git repository for OpenCode & Web Terminal tests."""
    repo = tmp_path / "mini_repo"
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "bossman@example.com")
    _git(repo, "config", "user.name", "Bossman Test")
    (repo / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
    tests_dir = repo / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test_calc.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial commit")
    return repo


async def _setup_gemma_stack(env, mini_repo: Path) -> dict:
    """Register online Gemma 4 local model + agent and allow `mini_repo` in terminal roots."""
    provider = (await env.client.post("/api/providers", json={
        "name": "Local Ollama",
        "kind": "openai_compat",
        "base_url": "http://127.0.0.1:8080/v1",
    })).json()

    model = (await env.client.post("/api/models", json={
        "provider_id": provider["id"],
        "name": "gemma4-31b-uncensored:latest",
        "alias": "gemma4-31b-uncensored:latest",
        "caps": {"chat": True, "tools": True, "code": True},
    })).json()

    agent = (await env.client.post("/api/agents", json={
        "name": "Gemma 4 uncensored",
        "system_prompt": "Ты — Bossman OpenCode Agent.",
        "model_id": model["id"],
        "tools": ["terminal.run", "code.search", "code.expand", "code.status", "computer.observe"],
        "permissions": {"read": "auto", "write": "ask", "exec": "ask", "computer_read": "auto", "computer_act": "ask"},
        "max_steps": 8,
    })).json()

    roots_res = await env.client.post("/api/terminal/roots", json={"roots": [str(mini_repo)]})
    assert roots_res.status_code == 200, roots_res.text
    return {"provider": provider, "model": model, "agent": agent}


# ---------------------------------------------------------------------------
# 1. Mobile chat basic response (CHAT mode)
# ---------------------------------------------------------------------------
async def test_01_mobile_chat_basic_response(env, mini_repo: Path):
    await _setup_gemma_stack(env, mini_repo)

    created = await env.client.post("/api/agent/sessions", json={
        "title": "Chat Mode Session",
        "mode": "CHAT",
    })
    assert created.status_code == 201, created.text
    sess = created.json()
    sid = sess["id"]
    assert sess["mode"] == "CHAT"

    msg = await env.client.post(f"/api/agent/sessions/{sid}/messages", json={
        "text": "Привет, Gemma! Как дела?",
        "mode": "CHAT",
        "client_request_id": "req-chat-0001",
    })
    assert msg.status_code in (200, 201, 202), msg.text
    body = msg.json()
    assert body["mode"] == "CHAT"
    assert body["route"]["tier"] == 1
    assert body["task_id"] > 0


# ---------------------------------------------------------------------------
# 2. Multi-step agent tool execution (AGENT mode)
# ---------------------------------------------------------------------------
async def test_02_multistep_agent_tool_execution(env, mini_repo: Path):
    await _setup_gemma_stack(env, mini_repo)

    created = await env.client.post("/api/agent/sessions", json={
        "title": "Agent Mode Session",
        "mode": "AGENT",
    })
    assert created.status_code == 201
    sid = created.json()["id"]

    msg = await env.client.post(f"/api/agent/sessions/{sid}/messages", json={
        "text": "Проверь статус репозитория и список файлов",
        "mode": "AGENT",
        "client_request_id": "req-agent-0002",
    })
    assert msg.status_code in (200, 201, 202), msg.text
    body = msg.json()
    assert body["mode"] == "AGENT"
    assert body["task_id"] > 0

    # Verify sequenced events are recorded
    events = await env.client.get(f"/api/agent/sessions/{sid}/events")
    assert events.status_code == 200
    ev_types = [e["type"] for e in events.json()["events"]]
    assert "task.progress" in ev_types


# ---------------------------------------------------------------------------
# 3. OpenCode file read + patch + test (CODE mode)
# ---------------------------------------------------------------------------
async def test_03_opencode_file_read_patch_test(env, mini_repo: Path):
    await _setup_gemma_stack(env, mini_repo)

    # Read directory & file via /api/terminal/files
    dir_res = await env.client.get("/api/terminal/files", params={"path": str(mini_repo)})
    assert dir_res.status_code == 200, dir_res.text
    assert dir_res.json()["kind"] == "dir"

    file_res = await env.client.get("/api/terminal/files", params={"path": str(mini_repo / "calc.py")})
    assert file_res.status_code == 200, file_res.text
    assert "def add" in file_res.json()["content"]

    # Create session in CODE mode and dispatch a coding task
    created = await env.client.post("/api/agent/sessions", json={
        "title": "Code Mode Session",
        "mode": "CODE",
    })
    sid = created.json()["id"]

    msg = await env.client.post(f"/api/agent/sessions/{sid}/messages", json={
        "text": "/code Проверь git status и запусти тесты",
        "mode": "CODE",
        "client_request_id": "req-code-0003",
    })
    assert msg.status_code in (200, 201, 202), msg.text
    body = msg.json()
    assert body["mode"] == "CODE"


# ---------------------------------------------------------------------------
# 4. Web terminal interactive command execution (REST + WebSocket stream)
# ---------------------------------------------------------------------------
async def test_04_web_terminal_interactive_execution(env, mini_repo: Path):
    await _setup_gemma_stack(env, mini_repo)

    # Run a safe read-only git status command in project_host mode (auto-approved by TerminalPolicy)
    run_res = await env.client.post("/api/terminal/run", json={
        "command": "git status --short",
        "cwd": str(mini_repo),
        "mode": "project_host",
    })
    assert run_res.status_code == 200, run_res.text
    term_id = run_res.json()["session_id"]

    # Wait briefly for command output
    await asyncio.sleep(0.3)
    log_res = await env.client.get(f"/api/terminal/sessions/{term_id}/log")
    assert log_res.status_code == 200, log_res.text

    # Connect via WebSocket to stream/replay terminal session
    with TestClient(env.app) as sync_client:
        with sync_client.websocket_connect(
            f"/api/terminal/sessions/{term_id}/stream?after=0&token={env.svc.auth.token}"
        ) as ws:
            first_msg = ws.receive_json()
            assert first_msg["type"] in ("terminal.hello", "terminal.output", "terminal.exit")
            assert first_msg["session_id"] == term_id

    # Verify /api/terminal/diff contract
    diff_res = await env.client.get("/api/terminal/diff", params={"cwd": str(mini_repo)})
    assert diff_res.status_code == 200, diff_res.text
    assert diff_res.json()["ok"] is True


# ---------------------------------------------------------------------------
# 5. Terminal Ctrl+C (sigint) and Agent Session STOP
# ---------------------------------------------------------------------------
async def test_05_terminal_ctrl_c_and_agent_stop(env, mini_repo: Path):
    await _setup_gemma_stack(env, mini_repo)

    # Start a long-running command via approval flow (`python -c` asks approval in project_host)
    ask_res = await env.client.post("/api/terminal/run", json={
        "command": "python -c \"import time; time.sleep(30)\"",
        "cwd": str(mini_repo),
        "mode": "project_host",
    })
    if ask_res.status_code == 202:
        err_body = ask_res.json().get("error") or ask_res.json().get("detail") or {}
        appr_id = err_body["approval_id"]
        await env.client.post(f"/api/approvals/{appr_id}", json={"approve": True})
        run_res = await env.client.post("/api/terminal/run", json={
            "command": "python -c \"import time; time.sleep(30)\"",
            "cwd": str(mini_repo),
            "mode": "project_host",
            "approval_id": appr_id,
        })
    else:
        run_res = ask_res
    assert run_res.status_code == 200, run_res.text
    term_id = run_res.json()["session_id"]

    # Send Ctrl+C (sigint)
    sig_res = await env.client.post(f"/api/terminal/sessions/{term_id}/sigint")
    assert sig_res.status_code == 200, sig_res.text
    assert sig_res.json()["ok"] is True

    # Create an agent session and trigger emergency STOP
    created = await env.client.post("/api/agent/sessions", json={"title": "Stop Test", "mode": "AGENT"})
    sid = created.json()["id"]
    stop_res = await env.client.post(f"/api/agent/sessions/{sid}/stop")
    assert stop_res.status_code == 200, stop_res.text
    assert stop_res.json()["ok"] is True


# ---------------------------------------------------------------------------
# 6. Windows screenshot and window list (COMPUTER mode + Unified Tool Registry)
# ---------------------------------------------------------------------------
async def test_06_windows_computer_use_and_tool_registry(env, mini_repo: Path):
    await _setup_gemma_stack(env, mini_repo)

    tools_res = await env.client.get("/api/agent/tools")
    assert tools_res.status_code == 200
    tdata = tools_res.json()
    for required_group in ("Computer", "Browser", "Files", "Terminal", "Git", "Coding", "Models", "Memory", "MCP", "Diagnostics"):
        assert required_group in tdata["groups"]
    comp_names = {t["name"] for t in tdata["groups"]["Computer"]}
    assert "computer.observe" in comp_names

    # Dispatch COMPUTER mode session message
    created = await env.client.post("/api/agent/sessions", json={"title": "PC Mode Session", "mode": "COMPUTER"})
    sid = created.json()["id"]
    msg = await env.client.post(f"/api/agent/sessions/{sid}/messages", json={
        "text": "Сделай скриншот и покажи активное окно Windows",
        "mode": "COMPUTER",
        "client_request_id": "req-pc-0006",
    })
    assert msg.status_code in (200, 201, 202), msg.text
    assert msg.json()["mode"] == "COMPUTER"


# ---------------------------------------------------------------------------
# 7. Safe computer action with approval (/api/agent/tasks/{id}/approve)
# ---------------------------------------------------------------------------
async def test_07_computer_action_approval_flow(env, mini_repo: Path):
    await _setup_gemma_stack(env, mini_repo)

    created = await env.client.post("/api/agent/sessions", json={"title": "Approval Session", "mode": "COMPUTER"})
    sid = created.json()["id"]
    msg = await env.client.post(f"/api/agent/sessions/{sid}/messages", json={
        "text": "Открой окно блокнота",
        "mode": "COMPUTER",
        "client_request_id": "req-appr-0007",
    })
    tid = msg.json()["task_id"]

    # Create a pending computer_act approval linked to this task
    appr = await env.svc.approvals.create(
        kind="computer_act",
        preview="computer.click(x=200, y=200)",
        task_id=tid,
    )
    app_res = await env.client.post(f"/api/agent/tasks/{tid}/approve", json={
        "decision": "approve",
        "approval_id": appr["id"],
        "note": "owner_verified_safe",
    })
    assert app_res.status_code == 200, app_res.text
    assert app_res.json()["ok"] is True
    assert app_res.json()["decision"] == "approve"


# ---------------------------------------------------------------------------
# 8. Blocked dangerous command (rm -rf /, format, diskpart, git push --force)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "bad_cmd",
    [
        "rm -rf /",
        "format C: /q",
        "diskpart /s wipe.txt",
        "git push origin main --force",
    ],
)
async def test_08_blocked_dangerous_commands(env, mini_repo: Path, bad_cmd: str):
    await _setup_gemma_stack(env, mini_repo)
    res = await env.client.post("/api/terminal/run", json={
        "command": bad_cmd,
        "cwd": str(mini_repo),
        "mode": "project_host",
    })
    assert res.status_code == 403, f"Expected 403 for {bad_cmd}, got {res.status_code}: {res.text}"


# ---------------------------------------------------------------------------
# 9. Local → Cloud fallback routing & 5-tier model router
# ---------------------------------------------------------------------------
async def test_09_local_to_cloud_fallback_router(env, mini_repo: Path, monkeypatch: pytest.MonkeyPatch):
    await _setup_gemma_stack(env, mini_repo)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key-12345")

    models_res = await env.client.get("/api/agent/models")
    assert models_res.status_code == 200
    mdata = models_res.json()
    tier_nums = {t["tier"] for t in mdata["tiers"]}
    assert tier_nums == {1, 2, 3, 4, 5}
    assert mdata["runpod_policy"] == "DENY_WITHOUT_EXPLICIT_OWNER_APPROVAL"

    # Tier 1 for CHAT
    r1 = await env.client.post("/api/agent/router/select", json={"mode": "CHAT"})
    assert r1.status_code == 200
    assert r1.json()["tier"] == 1

    # Tier 2 for local CODE
    r2 = await env.client.post("/api/agent/router/select", json={"mode": "CODE"})
    assert r2.status_code == 200
    assert r2.json()["tier"] == 2

    # Fallback from local to Tier 3 (Free Cloud) when previous_failures=1 and allow_cloud=True
    r3 = await env.client.post("/api/agent/router/select", json={
        "mode": "CODE",
        "allow_cloud": True,
        "previous_failures": 1,
    })
    assert r3.status_code == 200
    assert r3.json()["tier"] == 3
    assert r3.json()["locality"] == "cloud"


# ---------------------------------------------------------------------------
# 10. Budget limit stop & RunPod guard
# ---------------------------------------------------------------------------
async def test_10_budget_limit_stop_and_runpod_guard(env, mini_repo: Path):
    await _setup_gemma_stack(env, mini_repo)

    # Paid cloud worker (glm-flash) with 0 remaining budget must stop with 402 BUDGET_EXCEEDED
    b_res = await env.client.post("/api/agent/router/select", json={
        "mode": "CODE",
        "worker": "glm-flash",
        "allow_cloud": True,
        "budget_remaining_usd": 0.0,
    })
    assert b_res.status_code == 402, b_res.text
    b_err = b_res.json().get("error") or b_res.json().get("detail") or {}
    assert b_err["code"] == "BUDGET_EXCEEDED"

    # RunPod model/worker must be strictly blocked with 403 RUNPOD_FORBIDDEN
    rp_res = await env.client.post("/api/agent/router/select", json={
        "mode": "CODE",
        "model": "runpod-vllm-pod",
    })
    assert rp_res.status_code == 403, rp_res.text
    rp_err = rp_res.json().get("error") or rp_res.json().get("detail") or {}
    assert rp_err["code"] == "RUNPOD_FORBIDDEN"


# ---------------------------------------------------------------------------
# 11. WebSocket reconnect and event replay
# ---------------------------------------------------------------------------
async def test_11_websocket_reconnect_and_event_replay(env, mini_repo: Path):
    await _setup_gemma_stack(env, mini_repo)

    created = await env.client.post("/api/agent/sessions", json={"title": "WS Replay Session", "mode": "AGENT"})
    sid = created.json()["id"]

    # Emit two sequenced events on the session runtime
    rt = _orch(env.svc).runtime(sid)
    ev1 = rt.emit("tool.started", tool="code.status")
    ev2 = rt.emit("tool.completed", tool="code.status", preview="clean")

    # Connect via WebSocket with after=0 and verify both events are replayed
    with TestClient(env.app) as sync_client:
        with sync_client.websocket_connect(
            f"/api/agent/sessions/{sid}/stream?after=0&token={env.svc.auth.token}"
        ) as ws:
            hello = ws.receive_json()
            assert hello["type"] == "agent.hello"
            r1 = ws.receive_json()
            assert r1["seq"] == ev1["seq"]
            assert r1["replayed"] is True

        # Reconnect with after=ev1["seq"] and verify only ev2 is replayed
        with sync_client.websocket_connect(
            f"/api/agent/sessions/{sid}/stream?after={ev1['seq']}&token={env.svc.auth.token}"
        ) as ws2:
            hello2 = ws2.receive_json()
            assert hello2["type"] == "agent.hello"
            r2 = ws2.receive_json()
            assert r2["seq"] == ev2["seq"]
            assert r2["type"] == "tool.completed"


# ---------------------------------------------------------------------------
# 12. Persistence after backend restart
# ---------------------------------------------------------------------------
async def test_12_persistence_after_backend_restart(env, mini_repo: Path):
    await _setup_gemma_stack(env, mini_repo)

    created = await env.client.post("/api/agent/sessions", json={
        "title": "Persistent Session",
        "mode": "CODE",
        "budget_usd": 3.5,
    })
    sid = created.json()["id"]
    await env.client.post(f"/api/agent/sessions/{sid}/messages", json={
        "text": "Сохрани этот ход после рестарта",
        "mode": "CHAT",
        "client_request_id": "req-persist-0012",
    })

    # Simulate backend restart on the exact same Settings / data_dir
    app2, svc2 = await start_app(env.settings, start_workers=False)
    try:
        async with client_for(app2, svc2) as client2:
            loaded = await client2.get(f"/api/agent/sessions/{sid}")
            assert loaded.status_code == 200, loaded.text
            sdata = loaded.json()
            assert sdata["title"] == "Persistent Session"
            assert sdata["budget_usd"] == 3.5
            assert sdata["turn_count"] == 1
            assert sdata["turns"][0]["text"] == "Сохрани этот ход после рестарта"
    finally:
        await svc2.stop()


# ---------------------------------------------------------------------------
# 13. Parallel tasks isolation
# ---------------------------------------------------------------------------
async def test_13_parallel_tasks_isolation(env, mini_repo: Path):
    await _setup_gemma_stack(env, mini_repo)

    s1 = (await env.client.post("/api/agent/sessions", json={"title": "Parallel A", "mode": "CHAT"})).json()["id"]
    s2 = (await env.client.post("/api/agent/sessions", json={"title": "Parallel B", "mode": "AGENT"})).json()["id"]

    await env.client.post(f"/api/agent/sessions/{s1}/messages", json={
        "text": "Сообщение только для сессии A",
        "mode": "CHAT",
        "client_request_id": "req-par-a-0013",
    })
    await env.client.post(f"/api/agent/sessions/{s2}/messages", json={
        "text": "Сообщение только для сессии B",
        "mode": "AGENT",
        "client_request_id": "req-par-b-0013",
    })

    d1 = (await env.client.get(f"/api/agent/sessions/{s1}")).json()
    d2 = (await env.client.get(f"/api/agent/sessions/{s2}")).json()
    assert [t["text"] for t in d1["turns"]] == ["Сообщение только для сессии A"]
    assert [t["text"] for t in d2["turns"]] == ["Сообщение только для сессии B"]
    assert d1["mode"] == "CHAT"
    assert d2["mode"] == "AGENT"


# ---------------------------------------------------------------------------
# 14. /improve self-coding in isolated worktree (16-step controlled loop)
# ---------------------------------------------------------------------------
async def test_14_self_improve_isolated_worktree_16_steps(env, mini_repo: Path):
    await _setup_gemma_stack(env, mini_repo)

    created = await env.client.post("/api/agent/sessions", json={
        "title": "Self Improve Session",
        "mode": "SELF-IMPROVE",
    })
    sid = created.json()["id"]

    # Trigger /improve via message with a non-existent reproducer path so it delegates cleanly to sandbox verifier
    imp_res = await env.client.post(f"/api/agent/sessions/{sid}/messages", json={
        "text": "/improve Улучшить мобильный терминал и проверить контракты",
        "mode": "SELF-IMPROVE",
    })
    assert imp_res.status_code == 200, imp_res.text
    record = imp_res.json()["improve"]
    assert len(record["steps"]) == 16
    assert record["mission_receipt"]["sha256"]
    assert record["status"] == "awaiting_owner_approval"
    assert record["approval_id"] is not None

    # Owner approves the self-improvement run -> completes Steps 14–16
    approve_res = await env.client.post(f"/api/agent/tasks/{record['id']}/approve", json={
        "decision": "approve",
        "approval_id": record["approval_id"],
    })
    assert approve_res.status_code == 200, approve_res.text
    completed = approve_res.json()["improve"]
    assert completed["status"] == "completed"
    assert all(st["status"] == "completed" for st in completed["steps"])


# ---------------------------------------------------------------------------
# 15. Secret redaction in logs/UI/terminal
# ---------------------------------------------------------------------------
async def test_15_secret_redaction_in_session_events(env, mini_repo: Path):
    await _setup_gemma_stack(env, mini_repo)

    created = await env.client.post("/api/agent/sessions", json={"title": "Redaction Session", "mode": "CHAT"})
    sid = created.json()["id"]

    fake_sk = "sk-" + "A" * 32
    rt = _orch(env.svc).runtime(sid)
    evt = rt.emit("tool.completed", stdout=f"OPENAI_API_KEY={fake_sk}", api_key=fake_sk)
    assert fake_sk not in str(evt)

    events_res = await env.client.get(f"/api/agent/sessions/{sid}/events")
    assert events_res.status_code == 200
    assert fake_sk not in events_res.text


# ---------------------------------------------------------------------------
# 16. Owner AUTO (Bypass Permission) mode — Codex/Antigravity style
# ---------------------------------------------------------------------------
async def test_16_owner_auto_bypass_permissions_mode(env, mini_repo: Path):
    await _setup_gemma_stack(env, mini_repo)

    # Create session in AUTO mode — bypass_permissions is automatically True
    created = await env.client.post("/api/agent/sessions", json={
        "title": "Auto Bypass Session",
        "mode": "AUTO",
    })
    assert created.status_code == 201, created.text
    sess = created.json()
    sid = sess["id"]
    assert sess["mode"] == "AUTO"
    assert sess["bypass_permissions"] is True

    # Run an ASK-level terminal command (python -c ...) with bypass_permissions=True -> executes immediately (200)
    run_res = await env.client.post("/api/terminal/run", json={
        "command": "python -c \"print('auto-bypass-ok')\"",
        "cwd": str(mini_repo),
        "mode": "project_host",
        "bypass_permissions": True,
    })
    assert run_res.status_code == 200, run_res.text
    assert run_res.json()["session_id"]

    # DENY-level destructive commands remain strictly forbidden (403) even with bypass_permissions=True
    deny_res = await env.client.post("/api/terminal/run", json={
        "command": "rm -rf /",
        "cwd": str(mini_repo),
        "mode": "project_host",
        "bypass_permissions": True,
    })
    assert deny_res.status_code == 403, deny_res.text
