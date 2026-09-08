"""OpenHands end to end with the real SDK and nothing of ours mocked.

Everything Bossman owns runs for real here: `OpenHandsClient` spawns the real
sidecar as a subprocess, the sidecar imports the real `openhands-sdk`, builds a
real `Agent`/`Conversation`, and the SDK dispatches its real `file_editor` and
`terminal` tools against a real git worktree. Bossman then derives the git
evidence itself, exactly as it would in production.

The single substitution is the model's weights: a local OpenAI-compatible
endpoint scripts the agent's turns, because a paid provider key is not present
in every environment where this must run. That is a deliberate, stated boundary
— it proves the contract and the security envelope, not that a real model picks
sensible actions. The live-provider acceptance stays a separate, honestly
reported step.

These tests SKIP, never pass vacuously, when the SDK is not installed. A green
run with nothing exercised would be the exact false completion claim the
OpenHands honest-status report exists to prevent.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from bossman.apprentice.openhands_client import (OpenHandsClient, OpenHandsError,
                                                 OpenHandsRequest)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from openhands_stub_provider import ScriptedProvider  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
SIDECAR = REPO_ROOT / "scripts" / "openhands_sidecar.py"
#: The sidecar needs its own 3.12+ interpreter with `openhands-sdk` installed.
#: `BOSSMAN_OPENHANDS_PYTHON` names it; the convergence run provisions
#: `.venv-oh` at the repository root.
_CANDIDATES = [os.environ.get("BOSSMAN_OPENHANDS_PYTHON") or "",
               str(REPO_ROOT / ".venv-oh" / "bin" / "python"),
               str(REPO_ROOT / ".venv-oh" / "Scripts" / "python.exe")]


def _sdk_python() -> str | None:
    for candidate in _CANDIDATES:
        if not candidate or not Path(candidate).exists():
            continue
        probe = subprocess.run(
            [candidate, "-c", "import openhands.sdk, openhands.tools; print('ok')"],
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "OPENHANDS_SUPPRESS_BANNER": "1"})
        if probe.returncode == 0 and "ok" in probe.stdout:
            return candidate
    return None


SDK_PYTHON = _sdk_python()
requires_sdk = pytest.mark.skipif(
    SDK_PYTHON is None,
    reason="openhands-sdk runtime not installed (set BOSSMAN_OPENHANDS_PYTHON)")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A real, clean, remote-less git checkout — the only shape the client
    accepts."""
    ws = tmp_path / "ws"
    ws.mkdir()
    for args in (["init", "-q", "."], ["config", "user.email", "t@example.invalid"],
                 ["config", "user.name", "acceptance"]):
        subprocess.run(["git", "-C", str(ws), *args], check=True,
                       capture_output=True)
    (ws / "README.md").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(ws), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(ws), "commit", "-q", "-m", "init"], check=True,
                   capture_output=True)
    return ws


def client_for(provider: ScriptedProvider) -> OpenHandsClient:
    return OpenHandsClient(
        command=[SDK_PYTHON, str(SIDECAR)],
        env={"OPENROUTER_API_KEY": "stub-key-not-a-real-credential",
             "OPENROUTER_BASE_URL": provider.base_url,
             "OPENHANDS_SUPPRESS_BANNER": "1"})


def create_file(path: Path, text: str) -> dict:
    return {"tool": "file_editor",
            "arguments": {"command": "create", "path": str(path), "file_text": text}}


# --------------------------------------------------------------------- E2E

@requires_sdk
def test_the_real_sdk_edits_the_workspace_and_bossman_derives_the_evidence(workspace):
    """The whole path, un-mocked: real client, real subprocess, real SDK, real
    tool dispatch, real file on disk, real git diff."""
    plan = [create_file(workspace / "NOTES.md", "written by openhands\n"),
            {"text": "created NOTES.md"}]
    with ScriptedProvider(plan) as provider:
        result = client_for(provider).run(OpenHandsRequest(
            instruction="Create NOTES.md containing 'written by openhands'.",
            workspace=workspace, allowed_paths=("NOTES.md",),
            model="openrouter/stub-model", timeout_seconds=240))

    assert result.status == "completed"
    assert result.changed_files == ("NOTES.md",)
    assert "written by openhands" in result.diff
    # The file is really on disk — the evidence is not the sidecar's word.
    assert (workspace / "NOTES.md").read_text(encoding="utf-8") == "written by openhands\n"
    # And the SDK really ran an agent loop rather than short-circuiting.
    assert len(provider.requests) >= 2
    assert "file_editor" in provider.advertised_tools()


@requires_sdk
def test_the_sidecar_speaks_only_its_protocol_on_stdout(workspace):
    """Regression for a defect the installed package exposed: the SDK's
    conversation visualizer renders the system prompt and every model turn to
    STDOUT. That broke the JSON contract and leaked raw model reasoning into
    whatever captured the stream — both of which the sidecar docstring promises
    do not happen."""
    with ScriptedProvider([create_file(workspace / "A.md", "a\n"), {"text": "ok"}]) as provider:
        payload = {"schema": "bossman.openhands.v1", "instruction": "create A.md",
                   "workspace": str(workspace), "allowed_paths": ["A.md"],
                   "protected_paths": [], "model": "openrouter/stub-model", "metadata": {}}
        proc = subprocess.run(
            [SDK_PYTHON, str(SIDECAR)], input=json.dumps(payload), text=True,
            capture_output=True, timeout=240,
            env={**{k: os.environ[k] for k in ("PATH", "HOME", "LANG") if k in os.environ},
                 "OPENROUTER_API_KEY": "stub-key-not-a-real-credential",
                 "OPENROUTER_BASE_URL": provider.base_url,
                 "OPENHANDS_SUPPRESS_BANNER": "1"})

    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, f"stdout carried {len(lines)} lines, not just the response"
    body = json.loads(lines[0])
    assert body == {"schema": "bossman.openhands.v1", "status": "completed",
                    "model": "openrouter/stub-model"}
    assert "You are OpenHands agent" not in proc.stdout      # the system prompt stayed off stdout


@requires_sdk
def test_a_credential_never_reaches_stdout_or_the_agents_own_environment(workspace):
    """The key is popped from the environment before the SDK starts, so the
    agent's own terminal tool cannot read it back out of `env`."""
    secret = "stub-key-do-not-echo-1234567890"
    plan = [{"tool": "terminal", "arguments": {"command": "env | sort > env.txt"}},
            {"text": "dumped"}]
    with ScriptedProvider(plan) as provider:
        proc = subprocess.run(
            [SDK_PYTHON, str(SIDECAR)],
            input=json.dumps({"schema": "bossman.openhands.v1",
                              "instruction": "dump env", "workspace": str(workspace),
                              "allowed_paths": ["env.txt"], "protected_paths": [],
                              "model": "openrouter/stub-model", "metadata": {}}),
            text=True, capture_output=True, timeout=240,
            env={**{k: os.environ[k] for k in ("PATH", "HOME", "LANG") if k in os.environ},
                 "OPENROUTER_API_KEY": secret,
                 "OPENROUTER_BASE_URL": provider.base_url,
                 "OPENHANDS_SUPPRESS_BANNER": "1"})

    assert secret not in proc.stdout
    dumped = workspace / "env.txt"
    if dumped.exists():                       # the tool ran; check what it could see
        assert secret not in dumped.read_text(encoding="utf-8", errors="replace")


# ------------------------------------------------------- the security envelope

@requires_sdk
def test_an_edit_outside_the_allowed_paths_is_refused_after_the_fact(workspace):
    """OpenHands is a worker, not an authority. Bossman checks the real diff
    against the declared scope and refuses whatever the sidecar reported."""
    plan = [create_file(workspace / "SECRET.md", "out of scope\n"), {"text": "done"}]
    with ScriptedProvider(plan) as provider:
        with pytest.raises(OpenHandsError, match="out-of-scope"):
            client_for(provider).run(OpenHandsRequest(
                instruction="create SECRET.md", workspace=workspace,
                allowed_paths=("NOTES.md",), model="openrouter/stub-model",
                timeout_seconds=240))
    assert (workspace / "SECRET.md").exists()   # the edit happened; the RESULT is refused


@requires_sdk
def test_touching_a_protected_path_is_refused(workspace):
    """`file_editor create` refuses to clobber an existing file, so the agent
    reaches for the shell — which is exactly how a protected path gets touched
    in practice, and exactly what the post-run scope check must catch."""
    plan = [{"tool": "terminal", "arguments": {"command": "echo rewritten >> README.md"}},
            {"text": "done"}]
    with ScriptedProvider(plan) as provider:
        with pytest.raises(OpenHandsError, match="protected"):
            client_for(provider).run(OpenHandsRequest(
                instruction="rewrite README", workspace=workspace,
                allowed_paths=(".",), protected_paths=("README.md",),
                model="openrouter/stub-model", timeout_seconds=240))


@requires_sdk
def test_a_commit_by_the_agent_is_refused(workspace):
    """OpenHands may not move HEAD. If it commits, the run is not admissible
    evidence however clean the diff looks afterwards."""
    plan = [{"tool": "terminal", "arguments": {
        "command": "touch NOTES.md && git add -A && git -c user.email=a@b -c user.name=a commit -q -m sneaky"}},
        {"text": "committed"}]
    with ScriptedProvider(plan) as provider:
        with pytest.raises(OpenHandsError, match="HEAD"):
            client_for(provider).run(OpenHandsRequest(
                instruction="commit", workspace=workspace, allowed_paths=("NOTES.md",),
                model="openrouter/stub-model", timeout_seconds=240))


@requires_sdk
def test_adding_a_remote_is_refused(workspace):
    """No push authority means no remote, checked against the real repository
    rather than the sidecar's self-report."""
    plan = [{"tool": "terminal", "arguments": {
        "command": "git remote add origin https://example.invalid/x.git && touch NOTES.md"}},
        {"text": "added"}]
    with ScriptedProvider(plan) as provider:
        with pytest.raises(OpenHandsError, match="remote|configuration"):
            client_for(provider).run(OpenHandsRequest(
                instruction="add a remote", workspace=workspace,
                allowed_paths=("NOTES.md",), model="openrouter/stub-model",
                timeout_seconds=240))


@requires_sdk
def test_a_non_openrouter_model_never_runs_and_changes_nothing(workspace):
    """Provider credentials travel one supported path. A model id that is not
    OpenRouter never gets an API key attached, so the sidecar refuses before
    building an agent: `failed` status, no model contacted, nothing on disk."""
    with ScriptedProvider([{"text": "unused"}]) as provider:
        result = client_for(provider).run(OpenHandsRequest(
            instruction="anything", workspace=workspace, allowed_paths=("NOTES.md",),
            model="anthropic/claude-sonnet-4.5", timeout_seconds=120))
        assert provider.requests == [], "the model must never be contacted"
    assert result.status == "failed"
    assert result.changed_files == () and result.diff.strip() == ""


@requires_sdk
def test_a_run_that_changes_nothing_is_reported_as_changing_nothing(workspace):
    """Negative control: no invented evidence when the agent did no work."""
    with ScriptedProvider([{"text": "nothing to do"}]) as provider:
        result = client_for(provider).run(OpenHandsRequest(
            instruction="do nothing", workspace=workspace, allowed_paths=("NOTES.md",),
            model="openrouter/stub-model", timeout_seconds=240))
    assert result.status == "completed" and result.changed_files == ()
    assert result.diff.strip() == ""


def test_the_skip_is_honest_about_why(record_property):
    """Not a test of behaviour — a guard against a green run that exercised
    nothing. If the SDK is absent the suite above skips, and this records the
    reason so the acceptance report cannot quietly claim coverage."""
    record_property("openhands_sdk_python", SDK_PYTHON or "NOT_INSTALLED")
    if SDK_PYTHON is None:
        pytest.skip("openhands-sdk runtime not installed; live SDK E2E NOT_RUN")
    assert SIDECAR.exists()
