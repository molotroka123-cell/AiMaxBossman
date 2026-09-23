"""P1 C3: POST /api/terminal/roots must validate what it stores.

Owner-token model stays (no approval record), but the body is no longer
trusted blindly: `roots` must be a list of absolute paths to existing
directories, and a filesystem/drive root (`C:\\`, `/`) is refused. Invalid
input changes nothing — an independent GET still shows the old roots, and the
OpenCode roots (which fall back to `terminal.roots`) are not widened either.
"""
from __future__ import annotations

from pathlib import Path


def _drive_root() -> str:
    return Path.cwd().resolve().anchor or "/"


async def _roots(env) -> list[str]:
    r = await env.client.get("/api/terminal/roots")
    assert r.status_code == 200, r.text
    return r.json()["roots"]


async def _opencode_roots(env) -> list[str]:
    r = await env.client.get("/api/opencode/roots")
    assert r.status_code == 200, r.text
    return r.json()["roots"]


async def _seed(env, tmp_path) -> tuple[list[str], list[str]]:
    good = tmp_path / "c3_project"
    good.mkdir()
    r = await env.client.post("/api/terminal/roots", json={"roots": [str(good)]})
    assert r.status_code == 200, r.text
    return await _roots(env), await _opencode_roots(env)


async def _assert_refused(env, tmp_path, body) -> str:
    before, oc_before = await _seed(env, tmp_path)
    r = await env.client.post("/api/terminal/roots", json=body)
    assert r.status_code == 400, r.text
    assert await _roots(env) == before
    assert await _opencode_roots(env) == oc_before
    return r.text


async def test_drive_root_is_refused_and_nothing_widens(env, tmp_path):
    text = await _assert_refused(env, tmp_path, {"roots": [_drive_root()]})
    assert "корень диска" in text


async def test_string_instead_of_list_is_refused(env, tmp_path):
    await _assert_refused(env, tmp_path, {"roots": _drive_root()})


async def test_non_string_items_are_refused(env, tmp_path):
    await _assert_refused(env, tmp_path, {"roots": [123]})


async def test_relative_path_is_refused(env, tmp_path):
    await _assert_refused(env, tmp_path, {"roots": ["relative/dir"]})


async def test_missing_dir_is_refused(env, tmp_path):
    await _assert_refused(env, tmp_path, {"roots": [str(tmp_path / "nope")]})


async def test_file_instead_of_dir_is_refused(env, tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x", encoding="utf-8")
    await _assert_refused(env, tmp_path, {"roots": [str(f)]})


async def test_one_bad_item_rejects_the_whole_list(env, tmp_path):
    ok = tmp_path / "other_ok"
    ok.mkdir()
    await _assert_refused(env, tmp_path, {"roots": [str(ok), _drive_root()]})


async def test_valid_dirs_are_stored_resolved(env, tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    r = await env.client.post("/api/terminal/roots", json={"roots": [str(d)]})
    assert r.status_code == 200, r.text
    assert r.json()["roots"] == [str(d.resolve())]
    assert await _roots(env) == [str(d.resolve())]
