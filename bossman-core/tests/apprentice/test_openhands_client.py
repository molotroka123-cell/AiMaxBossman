from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from bossman.apprentice.openhands_client import OpenHandsClient, OpenHandsError, OpenHandsRequest


def _repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", str(tmp_path)], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Bossman Test"], check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.txt").write_text("before\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "base"], check=True, stdout=subprocess.DEVNULL)
    return tmp_path


def _sidecar(tmp_path: Path, target: str) -> Path:
    script = tmp_path / "fake_sidecar.py"
    script.write_text(
        "import json,sys,pathlib\n"
        "r=json.load(sys.stdin)\n"
        f"p=pathlib.Path(r['workspace'])/{target!r}\n"
        "p.parent.mkdir(parents=True,exist_ok=True);p.write_text('after\\n')\n"
        "print(json.dumps({'schema':'bossman.openhands.v1','status':'completed'}))\n"
    )
    return script


def test_accepts_real_in_scope_diff(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    sidecar = _sidecar(tmp_path, "src/a.txt")
    result = OpenHandsClient([sys.executable, str(sidecar)]).run(
        OpenHandsRequest("edit", repo, ("src",))
    )
    assert result.status == "completed"
    assert result.changed_files == ("src/a.txt",)
    assert "after" in result.diff


def test_rejects_out_of_scope_change(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    sidecar = _sidecar(tmp_path, "docs/pwn.txt")
    with pytest.raises(OpenHandsError, match="out-of-scope"):
        OpenHandsClient([sys.executable, str(sidecar)]).run(
            OpenHandsRequest("edit", repo, ("src",))
        )


def test_rejects_protected_change_even_if_allowed(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    sidecar = _sidecar(tmp_path, "src/a.txt")
    with pytest.raises(OpenHandsError, match="protected"):
        OpenHandsClient([sys.executable, str(sidecar)]).run(
            OpenHandsRequest("edit", repo, ("src",), ("src/a.txt",))
        )


def test_empty_allowlist_fails_closed(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    sidecar = _sidecar(tmp_path, "src/a.txt")
    with pytest.raises(OpenHandsError, match="allowed_paths"):
        OpenHandsClient([sys.executable, str(sidecar)]).run(
            OpenHandsRequest("edit", repo, ())
        )
