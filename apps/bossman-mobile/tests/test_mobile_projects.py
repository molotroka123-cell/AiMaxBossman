"""Owner 31.10: «функция проекты», «выбор проекта перед созданием чата», «хороший файл менеджер памяти».

The phone gateway proxies projects / project files / memory browser to the SAME Bossman (synthetic fake Bossman,
tests/conftest.py): session + CSRF on every call, slug and path validation BEFORE anything reaches Bossman, streamed
upload/download with nosniff, typed confirmation for delete, the chat created with the chosen project, and no vault path
of the memory config on the phone.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest

import conftest

UI = Path(__file__).resolve().parents[1] / "bossmobile" / "ui"
PROJECTS = {"root": "C:/Users/x/Bossman/Projects", "inbox": {"slug": "_inbox", "name": "Без проекта",
                                                             "path": "C:/Users/x/Bossman/Projects/_inbox"},
            "items": [{"slug": "мой-сайт", "name": "Мой сайт", "path": "C:/Users/x/Bossman/Projects/мой-сайт",
                       "threads": 2, "archived": False}]}


@pytest.fixture
def penv(tmp_path, monkeypatch):
    real = conftest.FakeBossman.handler

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = unquote(request.url.path), request.method
        q = dict(request.url.params)
        if path.startswith("/api/projects") or path.startswith("/api/memory"):
            self.calls.append({"method": method, "path": path, "query": q,
                               "body": request.content.decode("utf-8", "replace") if method != "POST" or not
                               path.endswith("/files") else f"<{len(request.content)} bytes>",
                               "headers": dict(request.headers)})
        if path == "/api/projects" and method == "GET":
            return httpx.Response(200, json=PROJECTS)
        if path == "/api/projects" and method == "POST":
            name = json.loads(request.content)["name"]
            return httpx.Response(201, json={"slug": "новый", "name": name, "path": "C:/P/новый", "threads": 0})
        if path == "/api/projects/мой-сайт" and method == "PATCH":
            return httpx.Response(200, json={"slug": "мой-сайт", **json.loads(request.content)})
        if path == "/api/projects/мой-сайт/files" and method == "GET":
            return httpx.Response(200, json={"project": "мой-сайт", "path": q.get("path", ""), "parent": None,
                                             "entries": [{"name": "app.py", "path": "app.py", "is_dir": False,
                                                          "size": 9, "mtime": "2026-10-31T10:00:00+01:00"}],
                                             "hidden": 1, "truncated": False})
        if path == "/api/projects/мой-сайт/files/download":
            if q.get("path") == "secret.env":
                return httpx.Response(403, json={"error": {"message": "этот файл недоступен", "code": "PROJECT_PATH_REFUSED"}})
            return httpx.Response(200, content=b"<html>x</html>", headers={
                "content-type": "text/html", "content-disposition": 'attachment; filename="index.html"'})
        if path == "/api/projects/мой-сайт/files" and method == "POST":
            self.uploaded = {"query": q, "size": len(request.content), "ctype": request.headers.get("content-type")}
            return httpx.Response(201, json={"ok": True, "project": "мой-сайт", "name": q["filename"],
                                             "path": q["filename"], "size": len(request.content), "sha256": "ab",
                                             "abs_secret": "C:/never"})
        if path == "/api/projects/мой-сайт/files" and method == "DELETE":
            if q.get("confirm") != "app.py":
                return httpx.Response(422, json={"error": {"message": "нужно подтверждение",
                                                           "code": "PROJECT_DELETE_CONFIRM_REQUIRED"}})
            return httpx.Response(200, json={"ok": True, "deleted": q["path"]})
        if path == "/api/memory/config":
            return httpx.Response(200, json={"configured": True, "root": "C:/Users/x/Obsidian/Private", "backend": "sqlite",
                                             "write_folder": "BOSSMAN Memory"})
        if path == "/api/memory/facts" and method == "GET":
            return httpx.Response(200, json={"items": [{"id": 7, "subject": "проект", "predicate": "стек",
                                                        "statement": "синтетический факт"}], "total": 1})
        if path == "/api/memory/facts/revoke":
            return httpx.Response(200, json={"revoked": [{"id": json.loads(request.content)["fact_id"]}], "total": 1})
        if path == "/api/memory/search":
            return httpx.Response(200, json={"query": "x", "items": [{"source": "a.md", "chunk_hash": "h1", "content": "c"}]})
        if path == "/api/memory/expand":
            return httpx.Response(200, json={"chunk_hash": "h1", "content": "полный текст"})
        return real(self, request)

    monkeypatch.setattr(conftest.FakeBossman, "handler", handler)
    env = conftest.Env(tmp_path)
    env.login()
    return env


def _bm(env, prefix):
    return [c for c in env.fake.calls if c["path"].startswith(prefix)]


ROUTES = [("get", "/m/api/projects"), ("post", "/m/api/projects"), ("patch", "/m/api/projects/мой-сайт"),
          ("get", "/m/api/projects/мой-сайт/files"), ("get", "/m/api/projects/мой-сайт/files/download?path=a"),
          ("post", "/m/api/projects/мой-сайт/files?filename=a.txt"),
          ("delete", "/m/api/projects/мой-сайт/files?path=a&confirm=a"), ("get", "/m/api/memory/status"),
          ("get", "/m/api/memory/facts"), ("post", "/m/api/memory/facts/7/forget"),
          ("post", "/m/api/memory/search"), ("post", "/m/api/memory/expand")]


def test_every_route_needs_a_session(tmp_path, penv):
    (tmp_path / "fresh").mkdir()
    fresh = conftest.Env(tmp_path / "fresh")
    for method, path in ROUTES:
        kw = {"json": {"name": "x", "query": "x", "chunk_hash": "h"}} if method in ("post", "patch") else {}
        assert getattr(fresh.c, method)(path, **kw).status_code in (401, 403), path


def test_mutating_routes_need_csrf(penv):
    before = len(_bm(penv, "/api/"))
    bad = {"X-BM-CSRF": "wrong"}
    for method, path in ROUTES:
        if method == "get":
            continue
        kw = {"json": {"name": "x", "query": "x", "chunk_hash": "h"}} if method in ("post", "patch") else {}
        assert getattr(penv.c, method)(path, headers=bad, **kw).status_code == 403, path
    assert len(_bm(penv, "/api/")) == before, "refused requests never reach Bossman"


def test_list_create_rename_archive_pass_through(penv):
    r = penv.c.get("/m/api/projects")
    assert r.status_code == 200 and r.json()["items"][0]["slug"] == "мой-сайт"
    r = penv.post("/m/api/projects", json={"name": "Новый"})
    assert r.status_code == 201 and r.json()["slug"] == "новый"
    r = penv.c.patch("/m/api/projects/мой-сайт", json={"archived": True}, headers={"X-BM-CSRF": penv.csrf})
    assert r.status_code == 200 and r.json()["archived"] is True
    patch = [c for c in _bm(penv, "/api/projects/") if c["method"] == "PATCH"][0]
    assert json.loads(patch["body"]) == {"archived": True}, "only the fields the phone set"


@pytest.mark.parametrize("slug", ["..", "a/b", "_secret", "x" * 41, "%2e%2e"])
def test_bad_slugs_never_reach_bossman(penv, slug):
    before = len(_bm(penv, "/api/projects"))
    r = penv.c.get(f"/m/api/projects/{slug}/files")
    assert r.status_code in (404, 422)
    assert len(_bm(penv, "/api/projects")) == before


@pytest.mark.parametrize("bad", ["../x", "a/../../x", "/etc/passwd", "C:/Windows/win.ini", "C:\\x", "..\\x"])
def test_paths_outside_the_project_never_reach_bossman(penv, bad):
    before = len(_bm(penv, "/api/projects"))
    assert penv.c.get("/m/api/projects/мой-сайт/files", params={"path": bad}).status_code == 422
    assert penv.c.get("/m/api/projects/мой-сайт/files/download", params={"path": bad}).status_code == 422
    assert penv.c.delete("/m/api/projects/мой-сайт/files", params={"path": bad, "confirm": "x"},
                         headers={"X-BM-CSRF": penv.csrf}).status_code == 422
    assert penv.post("/m/api/projects/мой-сайт/files", params={"filename": "a.txt", "dir": bad},
                     content=b"x").status_code == 422
    assert len(_bm(penv, "/api/projects")) == before


def test_listing_and_inbox(penv):
    r = penv.c.get("/m/api/projects/мой-сайт/files", params={"path": "src"})
    assert r.status_code == 200 and r.json()["entries"][0]["name"] == "app.py"
    assert _bm(penv, "/api/projects/мой-сайт/files")[-1]["query"] == {"path": "src"}
    assert penv.c.get("/m/api/projects/_inbox/files").status_code in (200, 404)    # valid slug: forwarded


def test_download_streams_with_nosniff_and_keeps_attachment(penv):
    r = penv.c.get("/m/api/projects/мой-сайт/files/download", params={"path": "index.html", "inline": 1})
    assert r.status_code == 200 and r.content == b"<html>x</html>"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-disposition"].startswith("attachment"), "html is never shown inline"
    assert _bm(penv, "/api/projects/мой-сайт/files/download")[-1]["query"] == {"path": "index.html", "inline": "true"}


def test_refused_download_reports_bossman_reason(penv):
    r = penv.c.get("/m/api/projects/мой-сайт/files/download", params={"path": "secret.env"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "PROJECT_PATH_REFUSED"


def test_upload_streams_raw_bytes(penv):
    blob = bytes(range(256)) * 64
    r = penv.post("/m/api/projects/мой-сайт/files", params={"filename": "data.bin", "dir": "src", "overwrite": 1},
                  content=blob, headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 201, r.text
    assert r.json() == {"ok": True, "project": "мой-сайт", "name": "data.bin", "path": "data.bin", "size": len(blob),
                        "sha256": "ab"}, "only known fields reach the phone"
    assert penv.fake.uploaded == {"query": {"filename": "data.bin", "dir": "src", "overwrite": "true"},
                                  "size": len(blob), "ctype": "application/octet-stream"}


def test_delete_needs_the_typed_name(penv):
    r = penv.c.delete("/m/api/projects/мой-сайт/files", params={"path": "app.py"}, headers={"X-BM-CSRF": penv.csrf})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "BM_CONFIRM"
    r = penv.c.delete("/m/api/projects/мой-сайт/files", params={"path": "app.py", "confirm": "nope"},
                      headers={"X-BM-CSRF": penv.csrf})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "PROJECT_DELETE_CONFIRM_REQUIRED"
    r = penv.c.delete("/m/api/projects/мой-сайт/files", params={"path": "app.py", "confirm": "app.py"},
                      headers={"X-BM-CSRF": penv.csrf})
    assert r.status_code == 200 and r.json() == {"ok": True, "deleted": "app.py"}


def test_new_chat_carries_the_chosen_project(penv):
    assert penv.post("/m/api/threads", json={"title": "t", "project": "мой-сайт"}).status_code == 201
    sent = [c for c in penv.fake.calls if c["path"] == "/api/chat/threads" and c["method"] == "POST"][-1]
    assert json.loads(sent["body"]) == {"title": "t", "project": "мой-сайт"}
    assert penv.post("/m/api/threads", json={"title": "t"}).status_code == 201
    sent = [c for c in penv.fake.calls if c["path"] == "/api/chat/threads" and c["method"] == "POST"][-1]
    assert json.loads(sent["body"]) == {"title": "t"}, "no project: the old payload"
    for bad in ("../x", "_inbox", "a/b"):
        assert penv.post("/m/api/threads", json={"title": "t", "project": bad}).status_code == 422


def test_memory_status_hides_the_vault_path(penv):
    r = penv.c.get("/m/api/memory/status")
    assert r.json() == {"configured": True, "backend": "sqlite", "write_folder": "BOSSMAN Memory"}
    assert "Obsidian" not in r.text


def test_memory_facts_search_view_forget(penv):
    facts = penv.c.get("/m/api/memory/facts", params={"query": "стек"}).json()
    assert facts["items"][0]["id"] == 7
    assert _bm(penv, "/api/memory/facts")[-1]["query"] == {"query": "стек", "limit": "50", "current_only": "true"}
    assert penv.post("/m/api/memory/facts/7/forget").json()["revoked"][0]["id"] == 7
    assert json.loads(_bm(penv, "/api/memory/facts/revoke")[-1]["body"]) == {"fact_id": 7}
    assert penv.post("/m/api/memory/facts/0/forget").status_code == 404
    assert penv.post("/m/api/memory/search", json={"query": "проект"}).json()["items"][0]["chunk_hash"] == "h1"
    assert penv.post("/m/api/memory/expand", json={"chunk_hash": "h1"}).json()["content"] == "полный текст"
    assert penv.post("/m/api/memory/expand", json={"chunk_hash": "../x"}).status_code == 422


def test_ui_has_the_project_picker_files_and_memory_views():
    js = (UI / "app.js").read_text(encoding="utf-8")
    html = (UI / "index.html").read_text(encoding="utf-8")
    for needle in ("function openProjectPicker", "function openProjectFiles", "function openMemoryBrowser",
                   "'/projects'", "/memory/facts", "project: S.project"):
        assert needle in js, needle
    for needle in ('id="project-bar"', 'id="drawer-files"', 'id="drawer-memory"', 'id="thread-filter"'):
        assert needle in html, needle
    sw = (UI / "sw.js").read_text(encoding="utf-8")
    assert "bossman-shell-v38" in sw
