"""tools/archive_migrate.py: a copy is accepted only when EVERY file matches by sha256; the pointer moves only then.

Synthetic trees under tmp_path. The same-size/different-bytes case is the real incident of 23.10: an interrupted copy
leaves a file of the right size whose content is wrong.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import archive_migrate as am  # noqa: E402


def _tree(root: Path) -> None:
    (root / "raw" / "v1").mkdir(parents=True)
    (root / "raw" / "v1" / "a.bin").write_bytes(b"A" * 5000)
    (root / "raw" / "v1" / "b.txt").write_text("hello", encoding="utf-8")
    (root / "queue.json").write_text('{"n": 1}', encoding="utf-8")


def _pointer(path: Path, root: Path) -> None:
    path.write_text(json.dumps({"schema": "x", "current_physical_root": str(root)}), encoding="utf-8")


def _copy_ok(src: Path, dst: Path) -> int:
    shutil.copytree(src, dst, dirs_exist_ok=True)
    return 1


def test_a_faithful_copy_passes_and_switches_the_pointer_but_keeps_the_source(tmp_path):
    src, dst, ptr = tmp_path / "C" / "k", tmp_path / "D" / "k", tmp_path / "storage.json"
    _tree(src)
    _pointer(ptr, src)
    r = am.migrate(src, dst, pointer=ptr, copy=_copy_ok, workers=2)
    assert r["verdict"] == "PASS" and r["comparison"]["identical"] is True and r["deleted_anything"] is False
    assert r["before"]["manifest_sha256"] == r["after"]["manifest_sha256"] and r["before"]["files"] == 3
    doc = json.loads(ptr.read_text(encoding="utf-8"))
    assert doc["current_physical_root"] == str(dst) and doc["previous_physical_root"] == str(src)
    assert (src / "raw" / "v1" / "a.bin").is_file(), "the source stays as the reserve"


def test_same_size_wrong_bytes_is_caught_and_the_pointer_does_not_move(tmp_path):
    src, dst, ptr = tmp_path / "C" / "k", tmp_path / "D" / "k", tmp_path / "storage.json"
    _tree(src)
    _pointer(ptr, src)

    def torn_copy(s, d):
        shutil.copytree(s, d)
        (d / "raw" / "v1" / "a.bin").write_bytes(b"A" * 4999 + b"Z")        # right size, one wrong byte
        return 1
    r = am.migrate(src, dst, pointer=ptr, copy=torn_copy, workers=2)
    assert r["verdict"] == "FAIL" and r["comparison"]["changed"]["first"] == ["raw/v1/a.bin"]
    assert json.loads(ptr.read_text(encoding="utf-8"))["current_physical_root"] == str(src)


def test_a_missing_and_an_extra_file_are_both_reported(tmp_path):
    src, dst = tmp_path / "C" / "k", tmp_path / "D" / "k"
    _tree(src)

    def partial(s, d):
        shutil.copytree(s, d)
        (d / "queue.json").unlink()
        (d / "stray.txt").write_text("not in the source", encoding="utf-8")
        return 1
    r = am.migrate(src, dst, copy=partial, workers=2)
    assert r["verdict"] == "FAIL"
    assert r["comparison"]["missing"]["first"] == ["queue.json"] and r["comparison"]["extra"]["first"] == ["stray.txt"]


def test_a_failed_copy_command_stops_before_any_comparison_or_pointer_change(tmp_path):
    src, dst, ptr = tmp_path / "C" / "k", tmp_path / "D" / "k", tmp_path / "storage.json"
    _tree(src)
    _pointer(ptr, src)
    r = am.migrate(src, dst, pointer=ptr, copy=lambda s, d: 16, workers=2)
    assert r["verdict"] == "FAIL" and "robocopy exit 16" in r["reason"] and "after" not in r
    assert json.loads(ptr.read_text(encoding="utf-8"))["current_physical_root"] == str(src)


def test_the_gate_runs_before_the_copy(tmp_path):
    src, dst = tmp_path / "C" / "k", tmp_path / "D" / "k"
    _tree(src)
    order = []
    am.migrate(src, dst, copy=lambda s, d: (order.append("copy"), _copy_ok(s, d))[1], gate=lambda: order.append("gate"), workers=2)
    assert order == ["gate", "copy"]


def test_manifest_hash_depends_on_content_not_on_order():
    a = {"x": {"size": 1, "sha256": "aa"}, "y": {"size": 2, "sha256": "bb"}}
    b = {"y": {"size": 2, "sha256": "bb"}, "x": {"size": 1, "sha256": "aa"}}
    assert am.manifest_hash(a) == am.manifest_hash(b)
    assert am.manifest_hash(a) != am.manifest_hash({**a, "y": {"size": 2, "sha256": "bc"}})
