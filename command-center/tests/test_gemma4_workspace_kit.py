"""Tests for Gemma 4 Autonomous Workspace Kit (5 layers + T3 privacy & security boundaries).

Covers:
  1. Indexed Workspace (`code.workspace_map` + BM25 `code.search` + `code.expand`):
     directory tree, dependencies (`pyproject.toml`, `package.json`, `requirements.txt`),
     and Git commit history.
  2. Strict Privacy Guard ("Локальные чаты и личные данные читать запрещено"):
     blocks Telegram (`tdata`), WhatsApp, Signal, Discord, browser `User Data`,
     `Pictures`/`Contacts`, `.env`, `.dpapi`, `provider-keys`, and `local_chats`.
  3. Terminal Access (`terminal.run`):
     `AUTO` for tests/git-read in project root, `ASK` for package install (`pip install`)
     and service management (`docker compose up`), `HARD_DENY` for destructive/offensive commands.
  4. Network Layer (`code.probe_endpoint`):
     safe `GET`/`HEAD` on allowed local dev ports, SSRF/LAN/metadata block, non-GET/HEAD refusal.
  5. Isolated Sandbox (`sandbox.run_check`) & Kit Provisioning (`/api/code/gemma-kit/provision`):
     runs `pytest` and `ast_check` with scrubbed secrets and disabled network; blocks traversal
     and shell metacharacters.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from bcc.features import tools_code, tools_terminal
from bcc.tools import ToolContext


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
def sample_workspace(tmp_path: Path) -> Path:
    repo = tmp_path / "gemma_proj"
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "gemma4@bossman.local")
    _git(repo, "config", "user.name", "Gemma 4")

    (repo / "pyproject.toml").write_text(
        '[project]\nname = "gemma-proj"\ndependencies = [\n  "fastapi>=0.110",\n  "httpx>=0.27",\n]\n',
        encoding="utf-8",
    )
    (repo / "package.json").write_text(
        json.dumps({"name": "gemma-ui", "dependencies": {"vite": "^5.0.0"}, "devDependencies": {"vitest": "^1.0.0"}}),
        encoding="utf-8",
    )
    (repo / "requirements.txt").write_text("pytest>=8.0\npydantic>=2.0\n", encoding="utf-8")

    src = repo / "src"
    src.mkdir()
    (src / "service.py").write_text(
        "def compute_health_score(ok: int, total: int) -> float:\n"
        "    if total <= 0:\n"
        "        return 0.0\n"
        "    return round(ok / total, 4)\n",
        encoding="utf-8",
    )

    tests_dir = repo / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_service.py").write_text(
        "import os\n"
        "from src.service import compute_health_score\n\n"
        "def test_compute_health_score():\n"
        "    assert compute_health_score(3, 4) == 0.75\n"
        "    assert 'SUPER_SECRET_API_TOKEN' not in os.environ\n"
        "    assert os.environ.get('NO_PROXY') == '*'\n",
        encoding="utf-8",
    )

    # Planted private chat & secret directories inside workspace to prove they are excluded from tree & blocked
    tdata = repo / "tdata"
    tdata.mkdir()
    (tdata / "chat_dump.py").write_text("PRIVATE_TELEGRAM_CHAT = 'do not read'\n", encoding="utf-8")

    _git(repo, "add", "pyproject.toml", "package.json", "requirements.txt", "src/service.py", "tests/test_service.py")
    _git(repo, "commit", "-m", "feat: bootstrap workspace for Gemma 4")
    return repo


async def test_workspace_map_dependencies_git_and_bm25(env, sample_workspace: Path):
    await tools_code._write_setting_json(env.svc, tools_code.CODE_ROOTS_KEY, [str(sample_workspace)])
    ctx = ToolContext(svc=env.svc, task={"id": 10}, run_id=10, agent={"id": 1}, workspace=str(sample_workspace))

    res = await tools_code.tool_code_workspace_map({"root": str(sample_workspace), "max_depth": 2, "git_commits": 5}, ctx)
    assert not res.error, res.content
    data = res.data
    assert data["root"] == str(sample_workspace.resolve())
    assert "src/" in data["tree"]
    assert "src/service.py" in data["tree"]
    # Private chat directory (`tdata`) must never appear in workspace tree
    assert not any("tdata" in entry.lower() for entry in data["tree"])

    # Dependencies parsed from pyproject.toml, package.json, requirements.txt
    assert "fastapi>=0.110" in data["dependencies"]["pyproject"]["pyproject.toml"]
    assert "vite" in data["dependencies"]["package_json"]["package.json"]["dependencies"]
    assert "pytest>=8.0" in data["dependencies"]["requirements"]["requirements.txt"]

    # Git commit history & HEAD SHA
    assert data["git"]["head_sha"]
    assert any("bootstrap workspace for Gemma 4" in c for c in data["git"]["recent_commits"])
    assert data["privacy_guard"]["local_chats_blocked"] is True

    # BM25 search finds compute_health_score
    search_res = await tools_code.tool_code_search({"query": "compute health score", "root": str(sample_workspace)}, ctx)
    assert not search_res.error, search_res.content
    assert "compute_health_score" in search_res.content


@pytest.mark.parametrize(
    "forbidden_path",
    [
        "C:/Users/asd/AppData/Roaming/Telegram Desktop/tdata",
        "C:/Users/asd/AppData/Local/WhatsApp",
        "C:/Users/asd/AppData/Roaming/Signal",
        "C:/Users/asd/AppData/Roaming/Discord",
        "C:/Users/asd/AppData/Local/Google/Chrome/User Data",
        "C:/Users/asd/Pictures/private_photo.jpg",
        "C:/Users/asd/Contacts",
        "C:/Users/asd/AppData/Local/Bossman/keys/provider-keys.env",
        "C:/Users/asd/AppData/Local/Bossman/keys/provider-keys.dpapi",
        "C:/Users/asd/Bossman/.env",
        "local_chats/export.json",
    ],
)
async def test_local_chats_and_personal_data_strictly_blocked(env, sample_workspace: Path, forbidden_path: str):
    await tools_code._write_setting_json(env.svc, tools_code.CODE_ROOTS_KEY, [str(sample_workspace)])
    ctx = ToolContext(svc=env.svc, task={"id": 11}, run_id=11, agent={"id": 1}, workspace=str(sample_workspace))

    blocked, reason = tools_code.is_private_chat_or_personal_path(forbidden_path)
    assert blocked is True
    assert "запрещён" in reason

    with pytest.raises(PermissionError, match="запрещён"):
        await tools_code.resolve_root(env.svc, forbidden_path, ctx=ctx)

    s_res = await tools_code.tool_code_search(
        {"query": "secret", "root": str(sample_workspace), "path_prefix": forbidden_path},
        ctx,
    )
    assert s_res.error is True
    assert "запрещён" in s_res.content

    sb_res = await tools_code.tool_sandbox_run_check(
        {"target": forbidden_path, "check_kind": "pytest", "root": str(sample_workspace)},
        ctx,
    )
    assert sb_res.error is True
    assert "запрещён" in sb_res.content


def test_terminal_policy_auto_ask_and_hard_deny_boundaries():
    # AUTO for tests & git read in sandbox
    assert tools_terminal.sandbox_auto_ok("pytest -q") is True
    assert tools_terminal.sandbox_auto_ok("python -m pytest tests -q") is True
    assert tools_terminal.sandbox_auto_ok("git status --short") is True
    assert tools_terminal.sandbox_auto_ok("git log -n 5 --oneline") is True

    # ASK for package installation, docker compose, and git push
    assert tools_terminal.extra_ask_reason("pip install requests") == "установка пакетов"
    assert tools_terminal.extra_ask_reason("npm install lodash") == "установка пакетов"
    assert tools_terminal.extra_ask_reason("docker compose up -d") == "управление сервисами"
    assert tools_terminal.extra_ask_reason("git push origin feature/x") == "публикация изменений"

    # HARD_DENY for destructive, credential-theft, and offensive commands
    assert tools_terminal.hard_deny_reason("git push --force origin main") == "force push"
    assert tools_terminal.hard_deny_reason("git reset --hard HEAD~1") == "деструктивный reset"
    assert tools_terminal.hard_deny_reason("cat ~/.ssh/id_rsa") == "доступ к учётным данным"
    assert tools_terminal.hard_deny_reason("nmap -sS 192.168.1.1") == "наступательные сетевые инструменты и эксплойты"


async def test_probe_endpoint_allowlist_and_ssrf_defense(env):
    # Allowed local project dev ports
    ok_8801, _ = tools_code.validate_probe_url("http://127.0.0.1:8801/health/live")
    ok_11434, _ = tools_code.validate_probe_url("http://127.0.0.1:11434/api/tags")
    assert ok_8801 is True
    assert ok_11434 is True

    # Unapproved local ports refused (e.g. SSH 22, RDP 3389, SMB 445)
    ok_22, reason_22 = tools_code.validate_probe_url("http://127.0.0.1:22/")
    assert ok_22 is False and "не входит в список разрешённых dev-портов" in reason_22

    # SSRF / cloud metadata / RFC1918 LAN refused
    ok_meta, _ = tools_code.validate_probe_url("http://169.254.169.254/latest/meta-data/")
    ok_lan, _ = tools_code.validate_probe_url("http://192.168.1.1/admin")
    ok_file, _ = tools_code.validate_probe_url("file:///C:/Windows/win.ini")
    assert ok_meta is False
    assert ok_lan is False
    assert ok_file is False

    # Non-GET/HEAD methods refused
    ctx = ToolContext(svc=env.svc, task={"id": 12}, run_id=12, agent={"id": 1})
    post_res = await tools_code.tool_code_probe_endpoint({"url": "http://127.0.0.1:8801/health/live", "method": "POST"}, ctx)
    assert post_res.error is True
    assert "GET и HEAD" in post_res.content


async def test_sandbox_run_check_isolated_no_secrets_no_network(env, sample_workspace: Path, monkeypatch):
    await tools_code._write_setting_json(env.svc, tools_code.CODE_ROOTS_KEY, [str(sample_workspace)])
    monkeypatch.setenv("SUPER_SECRET_API_TOKEN", "SYNTHETIC_TEST_ONLY_VALUE_DO_NOT_LEAK")
    ctx = ToolContext(svc=env.svc, task={"id": 13}, run_id=13, agent={"id": 1}, workspace=str(sample_workspace))

    # 1. AST check passes on valid python file
    ast_res = await tools_code.tool_sandbox_run_check(
        {"target": "src/service.py", "check_kind": "ast_check", "root": str(sample_workspace)},
        ctx,
    )
    assert not ast_res.error, ast_res.content
    assert ast_res.data["exit_code"] == 0

    # 2. Pytest check passes and verifies SUPER_SECRET_API_TOKEN is scrubbed & NO_PROXY=*
    py_res = await tools_code.tool_sandbox_run_check(
        {"target": "tests/test_service.py", "check_kind": "pytest", "root": str(sample_workspace)},
        ctx,
    )
    assert not py_res.error, py_res.content
    assert py_res.data["exit_code"] == 0
    assert py_res.data["network_isolated"] is True
    assert py_res.data["secrets_scrubbed"] is True

    # 3. Shell metacharacters and path traversal refused
    inj_res = await tools_code.tool_sandbox_run_check(
        {"target": "tests/test_service.py; whoami", "check_kind": "pytest", "root": str(sample_workspace)},
        ctx,
    )
    assert inj_res.error is True and "метасимволы" in inj_res.content

    trav_res = await tools_code.tool_sandbox_run_check(
        {"target": "../../outside.py", "check_kind": "pytest", "root": str(sample_workspace)},
        ctx,
    )
    assert trav_res.error is True and "выходит за пределы" in trav_res.content


async def test_gemma_kit_status_and_provision_endpoints(env, sample_workspace: Path):
    status_r = await env.client.get("/api/code/gemma-kit")
    assert status_r.status_code == 200, status_r.text
    st = status_r.json()
    assert st["model"] == "gemma4-31b-uncensored:latest"
    assert "code.workspace_map" in st["tools"]
    assert "code.probe_endpoint" in st["tools"]
    assert "sandbox.run_check" in st["tools"]
    assert "terminal.run" in st["tools"]
    assert "computer.observe" in st["tools"]
    assert "computer.act" in st["tools"]

    prov_r = await env.client.post(
        "/api/code/gemma-kit/provision",
        json={"model": "gemma4-31b-uncensored:latest", "agent_name": "Gemma 4 Workspace Engineer"},
    )
    assert prov_r.status_code == 200, prov_r.text
    body = prov_r.json()
    assert body["provisioned"] is True
    assert body["model_name"] == "gemma4-31b-uncensored:latest"
    assert body["agent_name"] == "Gemma 4 Workspace Engineer"
    assert body["privacy_guard"]["local_chats_blocked"] is True
    assert body["privacy_guard"]["personal_data_blocked"] is True
    assert body["privacy_guard"]["secrets_blocked"] is True
