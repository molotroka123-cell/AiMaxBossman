from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from bossman.apprentice.openhands_client import OpenHandsClient, OpenHandsError, OpenHandsRequest
from bossman.apprentice.openhands_teacher_client import OpenHandsTeacherClient


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _repo(tmp_path: Path, *, remote: bool = False) -> Path:
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    _git(root, "init"); _git(root, "config", "user.email", "test@example.invalid"); _git(root, "config", "user.name", "Bossman Test")
    (root / "src").mkdir(); (root / "src/a.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(root, "add", "."); _git(root, "commit", "-m", "base")
    if remote: _git(root, "remote", "add", "origin", "https://example.invalid/never.git")
    return root


def _sidecar(tmp_path: Path, target: str = "src/a.py", *, env_probe: str = "") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "fake_sidecar.py"
    probe = f"assert {env_probe!r} not in os.environ\n" if env_probe else ""
    script.write_text("import json,sys,pathlib,os\nr=json.load(sys.stdin)\n" + probe +
                      f"p=pathlib.Path(r['workspace'])/{target!r}\np.parent.mkdir(parents=True,exist_ok=True)\n"
                      "p.write_text('VALUE = 2\\n',encoding='utf-8')\n"
                      "print(json.dumps({'schema':'bossman.openhands.v1','status':'completed','model':r.get('model') or 'fake'}))\n", encoding="utf-8")
    return script


def test_real_subprocess_accepts_in_scope_diff(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    result = OpenHandsClient([sys.executable, str(_sidecar(tmp_path))]).run(OpenHandsRequest("edit", root, ("src",), model="openrouter/fake"))
    assert result.status == "completed" and result.changed_files == ("src/a.py",) and "VALUE = 2" in result.diff


def test_out_of_scope_and_protected_fail_closed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    with pytest.raises(OpenHandsError, match="out-of-scope"):
        OpenHandsClient([sys.executable, str(_sidecar(tmp_path, "docs/pwn.py"))]).run(OpenHandsRequest("edit", root, ("src",)))
    root2 = _repo(tmp_path / "second")
    with pytest.raises(OpenHandsError, match="protected"):
        OpenHandsClient([sys.executable, str(_sidecar(tmp_path / "second", "src/a.py"))]).run(OpenHandsRequest("edit", root2, ("src",), ("src/a.py",)))


def test_dirty_workspace_and_existing_remote_are_refused(tmp_path: Path) -> None:
    root = _repo(tmp_path); (root / "src/a.py").write_text("DIRTY = 1\n", encoding="utf-8")
    with pytest.raises(OpenHandsError, match="clean"):
        OpenHandsClient([sys.executable, str(_sidecar(tmp_path))]).run(OpenHandsRequest("x", root, ("src",)))
    root2 = _repo(tmp_path / "remote", remote=True)
    with pytest.raises(OpenHandsError, match="must not have git remotes"):
        OpenHandsClient([sys.executable, str(_sidecar(tmp_path / "remote"))]).run(OpenHandsRequest("x", root2, ("src",)))


def test_bossman_secret_environment_is_not_inherited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOSSMAN_PRIVATE_TEST_SECRET", "DO-NOT-LEAK")
    root = _repo(tmp_path)
    result = OpenHandsClient([sys.executable, str(_sidecar(tmp_path, env_probe="BOSSMAN_PRIVATE_TEST_SECRET"))]).run(OpenHandsRequest("edit", root, ("src",)))
    assert result.status == "completed"


def test_teacher_adapter_uses_sanitized_repo_and_returns_untrusted_patch(tmp_path: Path) -> None:
    fake = tmp_path / "teacher.py"
    fake.write_text("import json,sys,pathlib,subprocess\nr=json.load(sys.stdin);w=pathlib.Path(r['workspace'])\n"
                    "assert subprocess.check_output(['git','-C',str(w),'remote'],text=True).strip()==''\nassert not (w/'.env').exists()\n"
                    "(w/'src/a.py').write_bytes(b'VALUE = 7\\n')\n"  # exact bytes: Windows text mode would write CRLF

                    "print(json.dumps({'schema':'bossman.openhands.v1','status':'completed','model':'openrouter/anthropic/test'}))\n", encoding="utf-8")
    out = OpenHandsTeacherClient(OpenHandsClient([sys.executable, str(fake)]), model="openrouter/anthropic/test").run({
        "bundle_id":"b1","bug_description":"make seven","files":{"src/a.py":"VALUE = 1\n"},"failing_test":"VALUE == 7",
        "constraints":["no push"],"allowed_paths":["src/a.py"],"acceptance_tests":["tests/test_value.py"]})
    assert out["patch"] == {"src/a.py":"VALUE = 7\n"} and out["status"] == "UNTRUSTED_TEACHER_OUTPUT" and "mission_complete" not in out


def test_teacher_adapter_rejects_contract_tamper(tmp_path: Path) -> None:
    fake = tmp_path / "tamper.py"
    fake.write_text("import json,sys,pathlib\nr=json.load(sys.stdin);(pathlib.Path(r['workspace'])/'.bossman/OPENHANDS_TASK.json').write_text('{}')\n"
                    "print(json.dumps({'schema':'bossman.openhands.v1','status':'completed'}))\n", encoding="utf-8")
    with pytest.raises(OpenHandsError, match="protected/out-of-scope"):
        OpenHandsTeacherClient(OpenHandsClient([sys.executable, str(fake)])).run({"files":{"src/a.py":"x=1\n"},"allowed_paths":["src"],"acceptance_tests":[]})


def test_agent_cannot_hide_changes_with_commit_or_remote(tmp_path: Path) -> None:
    root = _repo(tmp_path); fake = tmp_path / "commit.py"
    fake.write_text("import json,sys,pathlib,subprocess\nr=json.load(sys.stdin);w=pathlib.Path(r['workspace']);(w/'src/a.py').write_text('VALUE = 3\\n')\n"
                    "subprocess.run(['git','-C',str(w),'add','.'],check=True);subprocess.run(['git','-C',str(w),'commit','-m','hide'],check=True,stdout=subprocess.DEVNULL)\n"
                    "print(json.dumps({'schema':'bossman.openhands.v1','status':'completed'}))\n", encoding="utf-8")
    with pytest.raises(OpenHandsError, match="commit/reset/rewrite"):
        OpenHandsClient([sys.executable, str(fake)]).run(OpenHandsRequest("x", root, ("src",)))
    root2 = _repo(tmp_path / "r2"); fake2 = tmp_path / "r2/remote.py"
    fake2.write_text("import json,sys,pathlib,subprocess\nr=json.load(sys.stdin);w=pathlib.Path(r['workspace']);subprocess.run(['git','-C',str(w),'remote','add','origin','https://example.invalid/x'],check=True)\n"
                     "print(json.dumps({'schema':'bossman.openhands.v1','status':'completed'}))\n", encoding="utf-8")
    with pytest.raises(OpenHandsError, match="git configuration|git remotes"):
        OpenHandsClient([sys.executable, str(fake2)]).run(OpenHandsRequest("x", root2, ("src",)))


def test_shipped_sidecar_matches_current_sdk_contract_without_network(tmp_path: Path) -> None:
    root = _repo(tmp_path); pkg = tmp_path / "fakepkg"; (pkg / "openhands/sdk").mkdir(parents=True)
    (pkg / "openhands/__init__.py").write_text(""); (pkg / "openhands/sdk/__init__.py").write_text(
        "import os\nfrom pathlib import Path\nclass LLM:\n def __init__(self,model,api_key=None,base_url=None): assert api_key=='test-key'\n"
        "class Tool:\n def __init__(self,name): self.name=name\nclass Agent:\n def __init__(self,llm,tools=None): pass\n"
        "class Conversation:\n def __init__(self,agent,workspace,visualizer=None,max_iteration_per_run=None): self.w=Path(workspace)\n def send_message(self,msg): pass\n"
        " def run(self): assert 'OPENROUTER_API_KEY' not in os.environ; (self.w/'src/a.py').write_text('VALUE = 9\\n')\n")
    tools = pkg / "openhands/tools"; tools.mkdir(); (tools / "__init__.py").write_text("")
    for mod, cls, name in (("file_editor","FileEditorTool","file_editor"),("task_tracker","TaskTrackerTool","task_tracker"),("terminal","TerminalTool","terminal")):
        (tools / f"{mod}.py").write_text(f"class {cls}:\n name={name!r}\n")
    shipped = Path(__file__).resolve().parents[3] / "scripts/openhands_sidecar.py"
    result = OpenHandsClient([sys.executable, str(shipped)], env={"PYTHONPATH":str(pkg),"OPENROUTER_API_KEY":"test-key"}).run(
        OpenHandsRequest("set nine", root, ("src",), model="openrouter/anthropic/fake"))
    assert result.status == "completed" and (root / "src/a.py").read_text() == "VALUE = 9\n"


def test_sidecar_failure_does_not_echo_exception_text(tmp_path: Path) -> None:
    root = _repo(tmp_path); shipped = Path(__file__).resolve().parents[3] / "scripts/openhands_sidecar.py"
    proc = subprocess.run([sys.executable, str(shipped)], input=json.dumps({"schema":"bossman.openhands.v1","workspace":str(root),"instruction":"x","model":None}),
                          text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={"PATH":os.environ.get("PATH","")})
    response = json.loads(proc.stdout)
    assert response["status"] == "failed" and set(response) == {"schema","status","error_type"}
