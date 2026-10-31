"""Owner 31.10 «функция проекты … отдельная папка … выбор проекта перед созданием чата» + «хороший файл менеджер».

Synthetic data only: every test has its own tmp BCC data dir and its own tmp projects root (conftest autouse
`_projects_root_off_the_real_machine`). Proved here:
  * projects API: create / list / rename / archive, slug-safe folder names, device/private names refused, duplicate 409,
    a root that overlaps Bossman's data refused;
  * the file manager is confined to the project folder: `..`, absolute paths, drive letters, junctions out, secrets
    (.env), Bossman data — refused or hidden; upload/download/delete with nosniff + confirm;
  * a chat in a project: its task gets workspace_path = the project folder and the prompt names the folder; a chat
    without a project uses `<root>/_inbox`; a cloud (chat-only) agent gets NO workspace_path and is told it has no
    file access;
  * end to end through the real engine: a local model's `code.write` call (relative path) lands in the project folder;
    the same call for `../escape.py` is refused and writes nothing outside;
  * terminal.run: the project folder is an allowed cwd for a local agent, and stays refused for a cloud agent.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

from bcc import conversation_context
from bcc.db import tasks as tasks_t
from bcc.features import chat_projects as cp
from bcc.tools import ToolContext

from .conftest import client_for, make_settings, start_app, wait_for
from .test_chat_agent_tool_use_e2e import FAST_ENGINE, ToolCallingModel


def _root() -> Path:
    return Path(os.environ["BCC_PROJECTS_ROOT"])


async def _local_stack(client, *, tools=None, base_url="http://127.0.0.1:8080/v1", name="аналитик") -> dict:
    provider = (await client.post("/api/providers", json={
        "name": f"prov-{name}", "kind": "openai_compat", "base_url": base_url})).json()
    model = (await client.post("/api/models", json={
        "provider_id": provider["id"], "name": f"m-{name}", "alias": f"m-{name}"})).json()
    agent = (await client.post("/api/agents", json={
        "name": name, "model_id": model["id"], "max_steps": 2, "tools": tools or []})).json()
    return {"provider": provider, "model": model, "agent": agent}


async def _project(env, name: str) -> dict:
    r = await env.client.post("/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


async def _task(env, task_id: int) -> dict:
    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(tasks_t).where(tasks_t.c.id == task_id))).mappings().first()
    return dict(row)


# ---------------------------------------------------------------- projects API

async def test_create_list_rename_archive(env):
    p = await _project(env, "Мой Сайт 2.0")
    assert p["slug"] == "мой-сайт-2-0" and p["name"] == "Мой Сайт 2.0" and p["exists"] is True
    folder = Path(p["path"])
    assert folder.is_dir() and folder.parent.resolve() == _root().resolve()
    reg = json.loads((env.settings.data_dir / "projects" / "projects.json").read_text(encoding="utf-8"))
    assert list(reg["items"]) == ["мой-сайт-2-0"], "registry lives in the Bossman data dir"

    listing = (await env.client.get("/api/projects")).json()
    assert [it["slug"] for it in listing["items"]] == ["мой-сайт-2-0"]
    assert listing["inbox"]["slug"] == "_inbox" and Path(listing["root"]) == _root().resolve()

    r = await env.client.patch(f"/api/projects/{p['slug']}", json={"name": "Сайт клиента"})
    assert r.status_code == 200 and r.json()["name"] == "Сайт клиента"
    assert r.json()["path"] == p["path"], "rename keeps the folder: chats and agent paths stay valid"

    r = await env.client.patch(f"/api/projects/{p['slug']}", json={"archived": True})
    assert r.json()["archived"] is True
    assert (await env.client.get("/api/projects")).json()["items"] == []
    assert [it["slug"] for it in (await env.client.get("/api/projects", params={"archived": 1})).json()["items"]] \
        == [p["slug"]]
    assert folder.is_dir(), "archive never deletes the folder"


@pytest.mark.parametrize("name,code", [
    ("CON", "PROJECT_NAME_INVALID"), ("lpt1", "PROJECT_NAME_INVALID"), ("Photos", "PROJECT_NAME_INVALID"),
    ("telegram", "PROJECT_NAME_INVALID"), ("...", "PROJECT_NAME_INVALID"), ("   ", "PROJECT_NAME_EMPTY")])
async def test_bad_names_are_refused(env, name, code):
    r = await env.client.post("/api/projects", json={"name": name})
    assert r.status_code == 422 and r.json()["error"]["code"] == code, r.text
    assert not any(_root().iterdir()) if _root().exists() else True


async def test_traversal_in_a_name_is_only_a_name(env):
    p = await _project(env, "../../evil")
    assert p["slug"] == "evil" and Path(p["path"]).parent.resolve() == _root().resolve()


async def test_duplicate_project_is_409(env):
    await _project(env, "Shop")
    r = await env.client.post("/api/projects", json={"name": "shop"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "PROJECT_EXISTS"


async def test_root_overlapping_bossman_data_is_refused(env, monkeypatch):
    monkeypatch.setenv("BCC_PROJECTS_ROOT", str(env.settings.data_dir / "projects-here"))
    r = await env.client.post("/api/projects", json={"name": "x"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "PROJECTS_ROOT_REFUSED"
    monkeypatch.setenv("BCC_PROJECTS_ROOT", str(env.settings.data_dir.parent))     # an ancestor of the data dir
    assert (await env.client.get("/api/projects")).status_code == 409


async def test_unknown_project_is_404_and_bad_slug_never_touches_disk(env):
    for slug in ("nope", "..", "CON"):
        r = await env.client.get(f"/api/projects/{slug}/files")
        assert r.status_code in (404, 422), (slug, r.text)


# ---------------------------------------------------------------- file manager

async def test_files_list_upload_download_delete(env):
    p = await _project(env, "files")
    up = await env.client.post(f"/api/projects/{p['slug']}/files", params={"filename": "notes.txt"},
                               content=b"hello")
    assert up.status_code == 201 and up.json()["path"] == "notes.txt" and up.json()["size"] == 5
    assert (Path(p["path"]) / "notes.txt").read_bytes() == b"hello"
    again = await env.client.post(f"/api/projects/{p['slug']}/files", params={"filename": "notes.txt"}, content=b"x")
    assert again.status_code == 409 and again.json()["error"]["code"] == "PROJECT_FILE_EXISTS"
    over = await env.client.post(f"/api/projects/{p['slug']}/files",
                                 params={"filename": "notes.txt", "overwrite": 1}, content=b"bye")
    assert over.status_code == 201 and (Path(p["path"]) / "notes.txt").read_bytes() == b"bye"

    (Path(p["path"]) / "src").mkdir()
    (Path(p["path"]) / "src" / "app.py").write_bytes(b"print(1)\n")
    lst = (await env.client.get(f"/api/projects/{p['slug']}/files")).json()
    assert [(e["name"], e["is_dir"]) for e in lst["entries"]] == [("src", True), ("notes.txt", False)]
    assert lst["path"] == "" and lst["parent"] is None
    sub = (await env.client.get(f"/api/projects/{p['slug']}/files", params={"path": "src"})).json()
    assert sub["entries"][0]["path"] == "src/app.py" and sub["entries"][0]["size"] == 9 and sub["parent"] == ""

    got = await env.client.get(f"/api/projects/{p['slug']}/files/download", params={"path": "src/app.py"})
    assert got.status_code == 200 and got.content == b"print(1)\n"
    assert got.headers["x-content-type-options"] == "nosniff"
    assert got.headers["content-disposition"].startswith("attachment")

    no = await env.client.delete(f"/api/projects/{p['slug']}/files", params={"path": "src/app.py"})
    assert no.status_code == 422 and no.json()["error"]["code"] == "PROJECT_DELETE_CONFIRM_REQUIRED"
    full = await env.client.delete(f"/api/projects/{p['slug']}/files", params={"path": "src", "confirm": "src"})
    assert full.status_code == 409 and full.json()["error"]["code"] == "PROJECT_DIR_NOT_EMPTY"
    ok = await env.client.delete(f"/api/projects/{p['slug']}/files", params={"path": "src/app.py", "confirm": "app.py"})
    assert ok.status_code == 200 and not (Path(p["path"]) / "src" / "app.py").exists()
    ok = await env.client.delete(f"/api/projects/{p['slug']}/files", params={"path": "src", "confirm": "src"})
    assert ok.status_code == 200 and not (Path(p["path"]) / "src").exists()
    root_del = await env.client.delete(f"/api/projects/{p['slug']}/files", params={"path": ".", "confirm": "."})
    assert root_del.status_code == 422 and Path(p["path"]).is_dir()


async def test_html_and_svg_are_never_inline(env):
    p = await _project(env, "web")
    for name in ("page.html", "pic.svg"):
        (Path(p["path"]) / name).write_text("<script>alert(1)</script>", encoding="utf-8")
        got = await env.client.get(f"/api/projects/{p['slug']}/files/download", params={"path": name, "inline": 1})
        assert got.headers["content-disposition"].startswith("attachment"), name
    (Path(p["path"]) / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    got = await env.client.get(f"/api/projects/{p['slug']}/files/download", params={"path": "a.png", "inline": 1})
    assert got.headers["content-disposition"].startswith("inline")


@pytest.mark.parametrize("bad", ["../x.txt", "src/../../x.txt", "/etc/passwd", "C:/Windows/win.ini",
                                 "C:\\Windows\\win.ini", "a.txt:stream", "NUL", "con.txt", "x. "])
async def test_paths_cannot_leave_the_project(env, bad):
    p = await _project(env, "jail")
    (_root() / "outside.txt").write_text("no", encoding="utf-8")
    for call in (env.client.get(f"/api/projects/{p['slug']}/files/download", params={"path": bad}),
                 env.client.get(f"/api/projects/{p['slug']}/files", params={"path": bad}),
                 env.client.delete(f"/api/projects/{p['slug']}/files", params={"path": bad, "confirm": "x"})):
        r = await call
        assert r.status_code in (403, 404, 422), (bad, r.status_code, r.text)
    up = await env.client.post(f"/api/projects/{p['slug']}/files", params={"filename": "ok.txt", "dir": bad},
                               content=b"x")
    assert up.status_code in (403, 404, 422), (bad, up.text)
    assert (_root() / "outside.txt").read_text(encoding="utf-8") == "no"


async def test_secrets_are_hidden_and_refused(env):
    p = await _project(env, "secrets")
    (Path(p["path"]) / ".env").write_text("OPENAI_API_KEY=sk-test", encoding="utf-8")
    (Path(p["path"]) / "readme.md").write_text("hi", encoding="utf-8")
    names = [e["name"] for e in (await env.client.get(f"/api/projects/{p['slug']}/files")).json()["entries"]]
    assert names == ["readme.md"]
    got = await env.client.get(f"/api/projects/{p['slug']}/files/download", params={"path": ".env"})
    assert got.status_code == 403 and got.json()["error"]["code"] == "PROJECT_PATH_REFUSED"
    up = await env.client.post(f"/api/projects/{p['slug']}/files", params={"filename": ".env"}, content=b"X=1")
    assert up.status_code == 403
    assert (Path(p["path"]) / ".env").read_text(encoding="utf-8") == "OPENAI_API_KEY=sk-test"


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are a Windows feature")
async def test_a_junction_out_of_the_project_is_hidden_and_refused(env, tmp_path):
    import _winapi
    p = await _project(env, "junction")
    outside = tmp_path / "outside-dir"
    outside.mkdir()
    (outside / "private.txt").write_text("private", encoding="utf-8")
    _winapi.CreateJunction(str(outside), str(Path(p["path"]) / "link"))
    lst = (await env.client.get(f"/api/projects/{p['slug']}/files")).json()
    assert lst["entries"] == [] and lst["hidden"] == 1
    got = await env.client.get(f"/api/projects/{p['slug']}/files/download", params={"path": "link/private.txt"})
    assert got.status_code == 403 and got.json()["error"]["code"] == "PROJECT_PATH_OUTSIDE"


async def test_inbox_is_browsable(env):
    r = await env.client.get("/api/projects/_inbox/files")
    assert r.status_code == 200 and r.json()["entries"] == []
    assert (_root() / "_inbox").is_dir()


# ---------------------------------------------------------------- chat -> workspace_path

async def _send(env, thread_id, agent_id, text="сделай файл"):
    r = await env.client.post(f"/api/chat/threads/{thread_id}/send",
                              json={"text": text, "agent_id": agent_id, "client_request_id": f"req-{thread_id}-1"})
    assert r.status_code in (200, 201, 202), r.text
    return r.json()


async def test_chat_in_a_project_gets_the_project_folder(env):
    stack = await _local_stack(env.client, tools=["code.write"])
    p = await _project(env, "Calc App")
    thread = (await env.client.post("/api/chat/threads", json={"project": p["slug"]})).json()
    assert thread["project"] == p["slug"]
    sent = await _send(env, thread["id"], stack["agent"]["id"], text="сделай калькулятор")
    task = await _task(env, sent["task"]["id"])
    assert task["workspace_path"] == str(Path(p["path"]).resolve())
    assert f"работай в папке проекта {Path(p['path']).resolve()}" in task["prompt"]
    assert "все файлы и код создавай там" in task["prompt"]
    # the line is CONTEXT, not the owner's request: the action router / contract still see only the typed text
    assert conversation_context.current_request(task["prompt"]) == "сделай калькулятор"
    listing = (await env.client.get("/api/projects")).json()["items"]
    assert listing[0]["threads"] == 1


async def test_second_turn_keeps_history_and_the_project_line(env):
    stack = await _local_stack(env.client, tools=["code.write"])
    p = await _project(env, "Two Turns")
    thread = (await env.client.post("/api/chat/threads", json={"project": p["slug"]})).json()
    first = await _send(env, thread["id"], stack["agent"]["id"], text="первый")
    async with env.svc.db.session() as s:          # the first turn finished, as a completed run leaves it
        from bcc.db import task_runs as runs_t
        await s.execute(sa.update(runs_t).where(runs_t.c.task_id == first["task"]["id"])
                        .values(status="completed", result="ок"))
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == first["task"]["id"]).values(status="completed"))
        await s.commit()
    r = await env.client.post(f"/api/chat/threads/{thread['id']}/send",
                              json={"text": "второй", "agent_id": stack["agent"]["id"],
                                    "client_request_id": f"req-{thread['id']}-2"})
    assert r.status_code == 200, r.text
    prompt = (await _task(env, r.json()["task"]["id"]))["prompt"]
    assert prompt.startswith(conversation_context.HEADER) and "Владелец: первый" in prompt
    assert prompt.count("работай в папке проекта") == 1
    assert conversation_context.current_request(prompt) == "второй"


async def test_chat_without_a_project_uses_the_inbox(env):
    coder = await _local_stack(env.client, tools=["terminal.run"], name="coder")
    thread = (await env.client.post("/api/chat/threads", json={})).json()
    sent = await _send(env, thread["id"], coder["agent"]["id"], text="создай заметку")
    task = await _task(env, sent["task"]["id"])
    assert task["workspace_path"] == str((_root() / "_inbox").resolve())
    assert "Рабочая папка этого чата (без проекта)" in task["prompt"]
    assert conversation_context.current_request(task["prompt"]) == "создай заметку"


async def test_tool_less_agent_without_a_project_is_unchanged(env):
    plain = await _local_stack(env.client, name="plain")
    thread = (await env.client.post("/api/chat/threads", json={})).json()
    sent = await _send(env, thread["id"], plain["agent"]["id"], text="привет")
    task = await _task(env, sent["task"]["id"])
    assert task["workspace_path"] is None and task["prompt"] == "привет", "no file tools: nothing changes"


async def test_cloud_agent_gets_no_folder_and_is_told_so(env):
    local = await _local_stack(env.client, tools=["code.write"], name="локальный")
    cloud = await _local_stack(env.client, tools=["code.write"], base_url="https://api.example-cloud.test/v1",
                               name="облако")
    assert cloud["agent"]["permissions"].get("cloud_pc_access") is False, "cloud agents are chat-only by default"
    p = await _project(env, "Cloud Proj")
    thread = (await env.client.post("/api/chat/threads", json={"project": p["slug"]})).json()
    prompt, ws = await cp.bind_chat_turn(env.svc, thread["id"], cloud["agent"]["id"], "текст")
    assert ws is None and "нет доступа к файлам ПК" in prompt and p["path"] in prompt
    assert conversation_context.current_request(prompt) == "текст"
    prompt, ws = await cp.bind_chat_turn(env.svc, thread["id"], local["agent"]["id"], "текст")
    assert ws == Path(p["path"]).resolve() and "работай в папке проекта" in prompt
    inbox_thread = (await env.client.post("/api/chat/threads", json={})).json()
    assert await cp.bind_chat_turn(env.svc, inbox_thread["id"], cloud["agent"]["id"], "текст") == ("текст", None)


async def test_ollama_cloud_tag_on_loopback_is_not_local(env):
    stack = await _local_stack(env.client, base_url="http://127.0.0.1:11434/v1", name="minimax")
    async with env.svc.db.session() as s:
        from bcc.db import models as models_t
        await s.execute(sa.update(models_t).where(models_t.c.id == stack["model"]["id"])
                        .values(name="minimax-m3.1-flash:cloud"))
        await s.commit()
    agent = (await env.client.get(f"/api/agents/{stack['agent']['id']}")).json()
    assert await cp.agent_works_locally(env.svc, agent) is False


# ---------------------------------------------------------------- tool grant: the project folder only

async def _ctx(env, agent: dict, workspace: Path) -> ToolContext:
    return ToolContext(svc=env.svc, task={"id": 1, "workspace_path": str(workspace)}, run_id=1, agent=agent,
                       step=0, workspace=str(workspace), call_id="c1")


async def test_grant_is_this_project_folder_for_a_local_agent_only(env):
    local = (await _local_stack(env.client, tools=["terminal.run"]))["agent"]
    cloud = (await _local_stack(env.client, tools=["terminal.run"], base_url="https://x.example.test/v1",
                                name="cloud"))["agent"]
    p = await _project(env, "grant")
    folder = Path(p["path"]).resolve()
    assert await cp.workspace_grant(await _ctx(env, local, folder)) == folder
    assert await cp.workspace_grant(await _ctx(env, cloud, folder)) is None
    assert await cp.workspace_grant(await _ctx(env, local, _root())) is None, "the root itself is not a project"
    stray = _root() / "not-registered"
    stray.mkdir(parents=True)
    assert await cp.workspace_grant(await _ctx(env, local, stray)) is None, "only registered projects or _inbox"
    elsewhere = env.settings.data_dir
    assert await cp.workspace_grant(await _ctx(env, local, elsewhere)) is None

    from bcc.features.tools_terminal import _run_context_deny
    assert await _run_context_deny({"command": "dir"}, await _ctx(env, local, folder)) is None
    denied = await _run_context_deny({"command": "dir"}, await _ctx(env, cloud, folder))
    assert denied and "вне разрешённых корней" in denied


CONTENT = "print('hi')\n"


async def _engine_write(tmp_path, rel: str, *, permissions: dict | None,
                        text: str = "создай hello.py") -> tuple[dict, Path, str]:
    """Chat in a project → real engine → the scripted local model calls code.write(path=rel). Returns the final task,
    the project folder and what the tool answered the model."""
    model = ToolCallingModel("code_write", {"path": rel, "content": CONTENT})
    base = await model.start()
    app, svc = await start_app(make_settings(tmp_path), start_workers=True, engine_options=FAST_ENGINE)
    try:
        async with client_for(app, svc) as client:
            prov = (await client.post("/api/providers", json={"name": "loc", "kind": "openai_compat",
                                                              "base_url": base})).json()
            mod = (await client.post("/api/models", json={"provider_id": prov["id"], "name": "qwen-scripted",
                                                          "alias": "qwen-scripted"})).json()
            agent = (await client.post("/api/agents", json={"name": "Coder", "model_id": mod["id"], "max_steps": 4,
                                                            "tools": ["code.write"],
                                                            "permissions": permissions or {}})).json()
            proj = (await client.post("/api/projects", json={"name": "Demo"})).json()
            thread = (await client.post("/api/chat/threads", json={"project": proj["slug"]})).json()
            sent = (await client.post(f"/api/chat/threads/{thread['id']}/send",
                                      json={"text": text, "agent_id": agent["id"],
                                            "client_request_id": "req-e2e-0001"})).json()

            async def done():
                t = (await client.get(f"/api/tasks/{sent['task']['id']}")).json()["task"]
                return t if t["status"] in {"completed", "failed", "stopped", "blocked", "waiting_approval"} else None

            final = await wait_for(done, timeout=20)
            tool_reply = json.dumps([m for r in model.requests[1:] for m in r["messages"] if m.get("role") == "tool"],
                                    ensure_ascii=False)
            return final, Path(proj["path"]), tool_reply
    finally:
        await svc.stop()
        await model.stop()


async def test_code_write_in_a_project_through_the_real_engine(tmp_path):
    """A local agent that holds code.write AND the owner's write permission: the relative path lands in the project."""
    final, folder, reply = await _engine_write(tmp_path, "hello.py", permissions={"terminal.run": True})
    assert final["status"] == "completed", final
    assert (folder / "hello.py").read_text(encoding="utf-8") == CONTENT
    assert "Записан файл hello.py" in reply


async def test_escape_from_the_project_is_refused_by_the_tool(tmp_path):
    final, folder, reply = await _engine_write(tmp_path, "../escape.py", permissions={"terminal.run": True})
    assert not (folder.parent / "escape.py").exists() and not (folder / "escape.py").exists()
    assert "code.write:" in reply and "Записан файл" not in reply, reply


async def test_the_independent_verifier_finds_the_file_in_the_project_folder(tmp_path):
    """«Создай … файл hello.py» makes finalize/review require file:hello.py; the verifier re-reads it in THIS task's
    project folder (found by the proof run 31.10: it looked only in the terminal roots and escalated to the owner)."""
    final, folder, _ = await _engine_write(
        tmp_path, "hello.py", permissions={"terminal.run": True},
        text="Создай в папке проекта файл hello.py с одной строкой print('hi'). Используй code.write.")
    assert (folder / "hello.py").is_file()
    assert final["status"] == "completed", final


async def test_without_the_write_permission_the_owner_is_still_asked(tmp_path):
    """The project folder is a working root, not an approval bypass: no permission → waiting_approval, no file."""
    final, folder, _ = await _engine_write(tmp_path, "hello.py", permissions={})
    assert final["status"] == "waiting_approval", final
    assert not (folder / "hello.py").exists()
