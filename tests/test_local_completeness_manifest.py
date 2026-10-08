"""tools/local_completeness_manifest.py: the owner-PC manifest that backs (or refuses) "nothing is lost"."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("local_completeness_manifest",
                                              ROOT / "tools" / "local_completeness_manifest.py")
lcm = importlib.util.module_from_spec(spec)
sys.modules["local_completeness_manifest"] = lcm
spec.loader.exec_module(lcm)

SECRET_VALUE = "sk-local-manifest-canary-0f3a"  # ci-secret-scan: allow (fake canary, must never be printed)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def make_pair(tmp_path: Path) -> tuple[Path, Path]:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    work = tmp_path / "Bossman" / "wt"
    work.mkdir(parents=True)
    git(work, "init", "-q", "-b", "main")
    git(work, "config", "user.email", "t@t")
    git(work, "config", "user.name", "t")
    (work / "a.py").write_text("x = 1\n", encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "init")
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "-q", "-u", "origin", "main")
    return work, origin


def test_a_clean_pushed_copy_is_complete(tmp_path):
    work, _ = make_pair(tmp_path)
    m = lcm.build_manifest([tmp_path / "Bossman"])
    assert m["LOCAL_COMPLETENESS"] == "COMPLETE"
    assert m["repositories"][0]["state"] == "CLEAN" and m["repositories"][0]["ahead"] == 0


def test_dirty_untracked_unpushed_and_stash_are_all_gaps(tmp_path):
    work, _ = make_pair(tmp_path)
    (work / "a.py").write_text("x = 2\n", encoding="utf-8")
    git(work, "stash", "-q")
    (work / "b.py").write_text("y = 1\n", encoding="utf-8")
    git(work, "add", "b.py")
    git(work, "commit", "-qm", "local only")
    (work / "new.txt").write_text("hello\n", encoding="utf-8")
    m = lcm.build_manifest([tmp_path / "Bossman"])
    repo = m["repositories"][0]
    assert m["LOCAL_COMPLETENESS"] == "GAPS"
    assert [c["subject"] for c in repo["unpushed_commits"]] == ["local only"]
    assert len(repo["stashes"]) == 1 and repo["ahead"] == 1
    row = next(f for f in repo["dirty_files"] if f["path"] == "new.txt")
    assert row["kind"] == "file" and len(row["sha256"]) == 64


def test_secret_like_and_weight_files_are_listed_but_never_opened(tmp_path, capsys):
    work, _ = make_pair(tmp_path)
    (work / "keys").mkdir()
    (work / "keys" / "provider-keys.env").write_text(f"OPENROUTER_API_KEY={SECRET_VALUE}\n", encoding="utf-8")
    (work / "model.safetensors").write_bytes(b"w" * 16)
    assert lcm.main(["--root", str(tmp_path / "Bossman"), "--out", str(tmp_path / "m.json")]) == 1
    text = (tmp_path / "m.json").read_text(encoding="utf-8") + capsys.readouterr().out
    assert SECRET_VALUE not in text
    rows = {f["path"]: f for f in json.loads((tmp_path / "m.json").read_text())["repositories"][0]["dirty_files"]}
    assert rows["keys/provider-keys.env"]["kind"] == "secret" and "sha256" not in rows["keys/provider-keys.env"]
    assert rows["model.safetensors"]["kind"] == "weights" and "sha256" not in rows["model.safetensors"]


def test_no_repository_found_is_unverified_not_complete(tmp_path):
    """Negative control: an empty scan must never read as 'nothing is lost'."""
    (tmp_path / "Bossman").mkdir()
    m = lcm.build_manifest([tmp_path / "Bossman"])
    assert m["LOCAL_COMPLETENESS"] == "UNVERIFIED"
    assert lcm.main(["--root", str(tmp_path / "Bossman")]) == 2


def test_installed_build_identity_is_read_from_the_bundle(tmp_path):
    bundle = tmp_path / "Bossman" / "app" / "BOSSMAN-Windows-x64-abc123"
    ident = bundle / "runtime" / "Lib" / "site-packages" / "bcc" / "_build.json"
    ident.parent.mkdir(parents=True)
    ident.write_text(json.dumps({"sha": "a" * 40, "built_at": "2026-10-08"}), encoding="utf-8")
    (tmp_path / "Bossman" / "app" / "BOSSMAN-Windows-x64-old").mkdir()
    builds = {b["folder"]: b for b in lcm.installed_builds([tmp_path / "Bossman"])}
    assert builds["BOSSMAN-Windows-x64-abc123"]["build_identity"]["sha"] == "a" * 40
    assert builds["BOSSMAN-Windows-x64-old"]["build_identity"] == "NOT_FOUND"
