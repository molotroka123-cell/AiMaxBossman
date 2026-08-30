"""P1-фиксы Lane-4 V2 (699c7d6) на CURRENT HEAD:

* P1-1: цепочка Session → Diff → Merge достижима через /api/coding-sessions
  (бэкенд — единственный CodingWorktreeManager, второго движка нет),
  source_repo конфайнен в канонические allowed_roots, конфликты → 409;
* P1-2/P1-3: /api/system больше не fake-green — пустой реестр моделей это
  'empty' (не ok), браузер с недоступным рантаймом — 'offline' (не ok),
  /api/browser/health отдаёт честное available.
"""
from pathlib import Path

import sqlalchemy as sa

from bcc.coding_session import _git
from bcc.db import settings_kv
from bcc.features.tools_code import CODE_ROOTS_KEY, _write_setting_json


async def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    await _git(path, "init", "-q")
    await _git(path, "config", "user.email", "t@t")
    await _git(path, "config", "user.name", "t")
    (path / "a.py").write_text("A = 1\n", encoding="utf-8")
    await _git(path, "add", "-A")
    await _git(path, "commit", "-q", "-m", "init")


async def _allow(env, *roots: Path) -> None:
    await _write_setting_json(env.svc, CODE_ROOTS_KEY, [str(r) for r in roots])


async def test_coding_session_rejects_repo_outside_allowed_roots(env, tmp_path):
    src = tmp_path / "outside" / "src"
    await _init_repo(src)
    r = await env.client.post("/api/coding-sessions", json={
        "session_id": "esc1", "source_repo": str(src)})
    assert r.status_code == 403, r.text


async def test_coding_session_diff_merge_chain(env, tmp_path):
    src = tmp_path / "src"
    await _init_repo(src)
    await _allow(env, tmp_path)

    r = await env.client.post("/api/coding-sessions", json={
        "session_id": "api1", "source_repo": str(src)})
    assert r.status_code == 200, r.text
    meta = r.json()
    assert meta["branch"] and Path(meta["worktree"]).exists()

    # реальное изменение в worktree → честный diff против запиненной базы
    wt = Path(meta["worktree"])
    (wt / "a.py").write_text("A = 2\n", encoding="utf-8")
    await _git(wt, "commit", "-aqm", "change")

    r = await env.client.get("/api/coding-sessions/api1/diff")
    assert r.status_code == 200, r.text
    d = r.json()
    assert "a.py" in d["files"] and "+A = 2" in d["patch"]

    r = await env.client.post("/api/coding-sessions/api1/merge_preview", json={})
    assert r.status_code == 200 and r.json()["clean"] is True, r.text

    r = await env.client.post("/api/coding-sessions/api1/merge", json={})
    assert r.status_code == 200 and r.json()["merged"] is True, r.text
    # merge двигает РЕАЛЬНЫЙ ref цели (не detached-сироту), рабочее дерево цело
    _, shown, _ = await _git(src, "show", "master:a.py")
    assert shown.strip() == "A = 2"
    assert (src / "a.py").read_text(encoding="utf-8").strip() == "A = 1"

    rows = (await env.client.get("/api/coding-sessions")).json()
    assert any(s["session_id"] == "api1" and s["status"] == "merged" for s in rows)


async def test_merge_conflict_is_policy_blocked_409(env, tmp_path):
    src = tmp_path / "src2"
    await _init_repo(src)
    await _allow(env, tmp_path)

    r = await env.client.post("/api/coding-sessions", json={
        "session_id": "api2", "source_repo": str(src)})
    assert r.status_code == 200, r.text
    wt = Path(r.json()["worktree"])
    (wt / "a.py").write_text("A = 'session'\n", encoding="utf-8")
    await _git(wt, "commit", "-aqm", "session change")
    (src / "a.py").write_text("A = 'main'\n", encoding="utf-8")
    await _git(src, "commit", "-aqm", "main change")

    r = await env.client.post("/api/coding-sessions/api2/merge", json={})
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["error"]["message"].startswith("merge отклонён")
    assert body["error"].get("conflicts")


async def test_system_health_no_fake_green(env):
    r = await env.client.get("/api/system")
    assert r.status_code == 200, r.text
    health = r.json()["health"]
    # пустой реестр моделей — 'empty', не ok (NO_EMPTY_TO_OK)
    assert health["models"]["status"] == "empty", health["models"]
    # браузер: любое честное состояние, но ключ есть и не притворяется ok без рантайма
    assert health["browser"]["status"] in ("ok", "offline", "unknown"), health["browser"]
    # базовые петли живы
    assert health["db"]["status"] == "ok"


async def test_browser_health_endpoint(env):
    r = await env.client.get("/api/browser/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["available"], bool)
    assert body["active_sessions"] == 0
