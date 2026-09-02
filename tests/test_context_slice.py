"""Context slices (tool-side, no model): repo map cached per sha; failing-test-first slice."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from context_slice import failing_test_slice, repo_map  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CC = ROOT / "command-center"


def _mini_repo(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "a.py").write_text("from pkg import b\n\ndef fa():\n    return b.fb()\n", encoding="utf-8")
    (tmp_path / "pkg" / "b.py").write_text("from pkg import c\n\ndef fb():\n    return c.X\n", encoding="utf-8")
    (tmp_path / "pkg" / "c.py").write_text("from pkg import d\nX = 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "d.py").write_text("Y = 2\n", encoding="utf-8")
    (tmp_path / "unrelated.py").write_text("class Z:\n    pass\n", encoding="utf-8")
    (tmp_path / "test_a.py").write_text("from pkg.a import fa\n\ndef test_fa():\n    assert fa() == 1\n", encoding="utf-8")
    return tmp_path


def test_repo_map_is_cached_per_sha_and_invalidated_by_sha(tmp_path):
    root = _mini_repo(tmp_path)
    first = repo_map(root, "sha1", cache_dir=tmp_path / "cache")
    assert first["cache"] == "miss" and set(first["files"]) >= {"pkg/a.py", "unrelated.py", "test_a.py"}
    assert first["files"]["unrelated.py"]["symbols"] == ["Z"]
    (root / "pkg" / "d.py").write_text("Y = 3\n", encoding="utf-8")          # изменение без нового sha
    second = repo_map(root, "sha1", cache_dir=tmp_path / "cache")
    assert second["cache"] == "hit" and second["files"] == first["files"]  # кэш по sha
    third = repo_map(root, "sha2", cache_dir=tmp_path / "cache")
    assert third["cache"] == "miss" and third["files"]["pkg/d.py"]["sha256"] != first["files"]["pkg/d.py"]["sha256"]
    assert (tmp_path / "cache" / "repo_map-sha2.json").exists()


def test_failing_test_slice_is_depth_bounded_and_hashed(tmp_path):
    root = _mini_repo(tmp_path)
    sl = failing_test_slice(root, root / "test_a.py", depth=2)
    paths = [f["path"] for f in sl["files"]]
    assert paths[0] == "test_a.py" and sl["files"][0]["reason"] == "failing-test"
    assert "pkg/a.py" in paths and "pkg/b.py" in paths          # depth 1 и 2
    assert "pkg/c.py" not in paths and "unrelated.py" not in paths  # depth 3 и несвязанное — нет
    import hashlib
    for f in sl["files"]:
        assert f["sha256"] == hashlib.sha256((root / f["path"]).read_bytes()).hexdigest()[:16]
    deeper = failing_test_slice(root, root / "test_a.py", depth=3)
    assert "pkg/c.py" in [f["path"] for f in deeper["files"]]
    assert sl == failing_test_slice(root, root / "test_a.py", depth=2)     # детерминизм


def test_real_secrem_test_slice_is_a_small_fraction_of_the_app():
    """Измерение (не оценка): срез для реального SECREM-теста против всего bcc."""
    sl = failing_test_slice(CC, CC / "tests" / "test_secrem_f015_self_assert.py", depth=2)
    full = repo_map(CC, "measure", cache_dir=Path("/tmp") / "bossman-map-test")
    ratio = sl["total_tokens"] / full["total_tokens"]
    assert 0 < ratio < 0.25, f"slice {sl['total_tokens']} vs app {full['total_tokens']} tokens (ratio {ratio:.2f})"
    print(json.dumps({"slice_tokens": sl["total_tokens"], "app_tokens": full["total_tokens"], "ratio": round(ratio, 3)}))
