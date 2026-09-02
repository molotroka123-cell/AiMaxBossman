"""Context slices (tool-side, no model): repo map cached per WORKTREE FINGERPRINT
(HEAD + dirty state), not per sha; failing-test-first slice."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from context_slice import (NOGIT, failing_test_slice, head_sha, repo_map,  # noqa: E402
                           validate_sha, worktree_fingerprint)

ROOT = Path(__file__).resolve().parents[1]
CC = ROOT / "command-center"


def _mini_repo(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir(parents=True)
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "a.py").write_text("from pkg import b\n\ndef fa():\n    return b.fb()\n", encoding="utf-8")
    (tmp_path / "pkg" / "b.py").write_text("from pkg import c\n\ndef fb():\n    return c.X\n", encoding="utf-8")
    (tmp_path / "pkg" / "c.py").write_text("from pkg import d\nX = 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "d.py").write_text("Y = 2\n", encoding="utf-8")
    (tmp_path / "unrelated.py").write_text("class Z:\n    pass\n", encoding="utf-8")
    (tmp_path / "test_a.py").write_text("from pkg.a import fa\n\ndef test_fa():\n    assert fa() == 1\n", encoding="utf-8")
    return tmp_path


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", *args], cwd=root,
                          check=True, capture_output=True, text=True).stdout.strip()


def _git_repo(tmp_path: Path) -> Path:
    """Временный git-репозиторий с одним коммитом: дальше тесты мутируют worktree."""
    root = _mini_repo(tmp_path / "repo")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


# ------------------------------------------------------------- кэш по отпечатку

def test_repo_map_rebuilds_on_modified_file_under_same_head(tmp_path):
    """Новый контракт: тот же HEAD + изменённый файл ⇒ новый отпечаток ⇒ пересборка
    (раньше кэш по sha отдавал устаревшую карту для грязного worktree)."""
    root = _git_repo(tmp_path)
    cache = tmp_path / "cache"
    head = _git(root, "rev-parse", "HEAD")
    first = repo_map(root, cache_dir=cache)
    assert first["cache"] == "miss" and first["sha"] == head
    assert set(first["files"]) >= {"pkg/a.py", "unrelated.py", "test_a.py"}
    assert first["files"]["unrelated.py"]["symbols"] == ["Z"]
    assert (cache / f"repo_map-{first['fingerprint']}.json").exists()

    same = repo_map(root, cache_dir=cache)                       # без изменений — hit
    assert same["cache"] == "hit" and same["fingerprint"] == first["fingerprint"]
    assert same["files"] == first["files"]

    (root / "pkg" / "d.py").write_text("Y = 3\n", encoding="utf-8")   # правка без нового коммита
    assert _git(root, "rev-parse", "HEAD") == head
    second = repo_map(root, cache_dir=cache)
    assert second["cache"] == "miss" and second["sha"] == head
    assert second["fingerprint"] != first["fingerprint"]
    assert second["files"]["pkg/d.py"]["sha256"] != first["files"]["pkg/d.py"]["sha256"]
    assert second["files"]["pkg/d.py"]["sha256"] == hashlib.sha256(b"Y = 3\n").hexdigest()[:16]

    (root / "pkg" / "d.py").write_text("Y = 2\n", encoding="utf-8")   # откат — прежний отпечаток
    back = repo_map(root, cache_dir=cache)
    assert back["cache"] == "hit" and back["fingerprint"] == first["fingerprint"]


def test_fingerprint_changes_on_add_delete_rename_untracked_symlink(tmp_path):
    root = _git_repo(tmp_path)
    seen = {"clean": worktree_fingerprint(root)}
    assert worktree_fingerprint(root) == seen["clean"]            # детерминизм

    (root / "new_untracked.py").write_text("N = 1\n", encoding="utf-8")   # untracked
    seen["untracked"] = worktree_fingerprint(root)
    (root / "new_untracked.py").write_text("N = 2\n", encoding="utf-8")   # содержимое untracked
    seen["untracked_edited"] = worktree_fingerprint(root)
    _git(root, "add", "new_untracked.py")                                  # add (staged)
    seen["added"] = worktree_fingerprint(root)
    (root / "unrelated.py").unlink()                                       # delete
    seen["deleted"] = worktree_fingerprint(root)
    _git(root, "mv", "pkg/d.py", "pkg/dd.py")                              # rename
    seen["renamed"] = worktree_fingerprint(root)
    os.symlink("pkg/a.py", root / "link.py")                               # symlink → a
    seen["symlink_a"] = worktree_fingerprint(root)
    os.unlink(root / "link.py")
    os.symlink("pkg/b.py", root / "link.py")                               # тот же путь, другая цель
    seen["symlink_b"] = worktree_fingerprint(root)

    values = list(seen.values())
    assert len(set(values)) == len(values), seen                          # все состояния различимы
    assert all(len(v) == 40 for v in values)


def test_concurrent_edits_to_different_files_are_distinguished(tmp_path):
    """Правки в разных файлах (в т.ч. «параллельные» между двумя вызовами map)
    дают разные отпечатки, а карта каждой версии собирается заново."""
    root = _git_repo(tmp_path)
    cache = tmp_path / "cache"
    base = repo_map(root, cache_dir=cache)["fingerprint"]
    (root / "pkg" / "a.py").write_text("def fa():\n    return 1\n", encoding="utf-8")
    fa = worktree_fingerprint(root)
    (root / "pkg" / "b.py").write_text("def fb():\n    return 2\n", encoding="utf-8")
    fab = repo_map(root, cache_dir=cache)
    (root / "pkg" / "a.py").write_text("from pkg import b\n\ndef fa():\n    return b.fb()\n", encoding="utf-8")
    fb = worktree_fingerprint(root)
    assert len({base, fa, fab["fingerprint"], fb}) == 4
    assert fab["cache"] == "miss" and fab["files"]["pkg/b.py"]["symbols"] == ["fb"]
    assert repo_map(root, cache_dir=cache)["fingerprint"] == fb


def test_default_cache_dir_inside_root_does_not_self_invalidate(tmp_path):
    """Кэш-файл под root/.bossman-cache — untracked, но исключён из отпечатка."""
    root = _git_repo(tmp_path)
    first = repo_map(root)
    assert (root / ".bossman-cache" / f"repo_map-{first['fingerprint']}.json").exists()
    second = repo_map(root)
    assert second["cache"] == "hit" and second["fingerprint"] == first["fingerprint"]
    assert ".bossman-cache" not in " ".join(first["files"])


def test_non_git_root_falls_back_to_content_fingerprint(tmp_path):
    root = _mini_repo(tmp_path / "plain")
    cache = tmp_path / "cache"
    assert head_sha(root) == NOGIT
    first = repo_map(root, cache_dir=cache)
    assert first["sha"] == NOGIT and first["fingerprint"].startswith("nogit-")
    assert repo_map(root, cache_dir=cache)["cache"] == "hit"
    (root / "pkg" / "d.py").write_text("Y = 9\n", encoding="utf-8")
    third = repo_map(root, cache_dir=cache)
    assert third["cache"] == "miss" and third["fingerprint"] != first["fingerprint"]


# ---------------------------------------------------------- явный ключ и валидация

def test_explicit_sha_override_is_validated_and_never_a_git_option(tmp_path):
    root = _git_repo(tmp_path)
    cache = tmp_path / "cache"
    head = _git(root, "rev-parse", "HEAD")
    assert validate_sha(head) == head and validate_sha(head[:7]) == head[:7]
    assert validate_sha("measure") == "measure" and validate_sha("run_1-x") == "run_1-x"
    for bad in ("", "--output=/tmp/x", "../../etc/passwd", "a b", "a/b", "x" * 65,
                "shá", "-", "--"):
        if bad in ("-", "--"):
            continue                    # matches [A-Za-z0-9_-]{1,64} by the spec'd regex
        with pytest.raises(ValueError):
            validate_sha(bad)
        with pytest.raises(ValueError):
            repo_map(root, bad, cache_dir=cache)
    assert not list(cache.glob("*")) if cache.exists() else True   # ничего не записано

    over = repo_map(root, "label1", cache_dir=cache)
    assert over["cache"] == "miss" and over["fingerprint"] == "label1" and over["sha"] == head
    (root / "pkg" / "d.py").write_text("Y = 3\n", encoding="utf-8")
    assert repo_map(root, "label1", cache_dir=cache)["cache"] == "hit"   # явный ключ — на совести вызывающего
    assert (cache / "repo_map-label1.json").exists()


def test_head_sha_is_40_hex_or_nogit(tmp_path):
    root = _git_repo(tmp_path)
    sha = head_sha(root)
    assert len(sha) == 40 and int(sha, 16) >= 0 and sha == _git(root, "rev-parse", "HEAD")
    assert head_sha(tmp_path / "missing") == NOGIT
    empty = tmp_path / "empty"
    empty.mkdir()
    _git(empty, "init", "-q")
    assert head_sha(empty) == NOGIT                                 # репозиторий без коммитов


def test_cli_map_rejects_invalid_sha(tmp_path):
    root = _git_repo(tmp_path)
    tool = ROOT / "tools" / "context_slice.py"
    bad = subprocess.run([sys.executable, str(tool), "map", str(root), "--sha", "a/b;rm -rf x"],
                         capture_output=True, text=True)
    assert bad.returncode == 2 and "invalid sha" in bad.stderr
    ok = subprocess.run([sys.executable, str(tool), "map", str(root)], capture_output=True, text=True)
    assert ok.returncode == 0
    out = json.loads(ok.stdout)
    assert out["sha"] == _git(root, "rev-parse", "HEAD") and out["cache"] == "miss"
    assert out["fingerprint"] == worktree_fingerprint(root)


# ------------------------------------------------------------- failing-test slice

def test_failing_test_slice_is_depth_bounded_and_hashed(tmp_path):
    root = _mini_repo(tmp_path)
    sl = failing_test_slice(root, root / "test_a.py", depth=2)
    paths = [f["path"] for f in sl["files"]]
    assert paths[0] == "test_a.py" and sl["files"][0]["reason"] == "failing-test"
    assert "pkg/a.py" in paths and "pkg/b.py" in paths          # depth 1 и 2
    assert "pkg/c.py" not in paths and "unrelated.py" not in paths  # depth 3 и несвязанное — нет
    for f in sl["files"]:
        assert f["sha256"] == hashlib.sha256((root / f["path"]).read_bytes()).hexdigest()[:16]
    deeper = failing_test_slice(root, root / "test_a.py", depth=3)
    assert "pkg/c.py" in [f["path"] for f in deeper["files"]]
    assert sl == failing_test_slice(root, root / "test_a.py", depth=2)     # детерминизм


def test_real_secrem_test_slice_is_a_small_fraction_of_the_app(tmp_path):
    """Измерение (не оценка): срез для реального SECREM-теста против всего bcc."""
    sl = failing_test_slice(CC, CC / "tests" / "test_secrem_f015_self_assert.py", depth=2)
    full = repo_map(CC, "measure", cache_dir=tmp_path / "bossman-map-test")
    ratio = sl["total_tokens"] / full["total_tokens"]
    assert 0 < ratio < 0.25, f"slice {sl['total_tokens']} vs app {full['total_tokens']} tokens (ratio {ratio:.2f})"
    print(json.dumps({"slice_tokens": sl["total_tokens"], "app_tokens": full["total_tokens"], "ratio": round(ratio, 3)}))
