from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bcc.db import Database, agents, models, providers
from bcc.owner_input import OwnerInputStore
from bcc.telegram_companion.config import Person, Settings
from bcc.telegram_companion.console import ConsoleMixin
from bcc.features import tools_browser


class Vault:
    def encrypt(self, text: str) -> str:
        return base64.b64encode(text.encode()).decode()[::-1]
    def decrypt(self, text: str) -> str:
        return base64.b64decode(text[::-1]).decode()


class Bus:
    def __init__(self): self.rows = []
    async def emit(self, kind, **data): self.rows.append((kind, data))


class Manager:
    def __init__(self):
        self.calls = []
    async def jev_current(self, sid, **kw):
        return {"url": "https://example.test/signup", "generation": 1, "refs": ["e1-0", "e1-1"], "items": {}}
    async def type_text(self, sid, selector, text, **kw):
        self.calls.append(("text", sid, selector, kw.get("ref"), text)); return {}
    async def fill_secret(self, sid, selector, *, secret, **kw):
        self.calls.append(("secret", sid, selector, kw.get("ref"), secret)); return {}


def test_owner_input_is_encrypted_and_erased_after_fill(tmp_path):
    store = OwnerInputStore(tmp_path / "requests.json", Vault())
    row = store.create(task_id=7, session_id=3, context="provider signup", success={"url_changed": True}, fields=[
        {"key": "email", "label": "Email", "ref": "e1-0"},
        {"key": "password", "label": "Password", "ref": "e1-1", "secret": True},
    ])
    store.answer(row["id"], {"email": "owner@example.test", "password": "S3cret!"}, actor="tg:owner")
    raw = (tmp_path / "requests.json").read_text(encoding="utf-8")
    assert "owner@example.test" not in raw and "S3cret!" not in raw
    saved, values = store.values_for_fill(row["id"], task_id=7)
    assert values == {"email": "owner@example.test", "password": "S3cret!"}
    assert saved["status"] == "ANSWERED"
    store.mark_filled(row["id"])
    raw = (tmp_path / "requests.json").read_text(encoding="utf-8")
    assert "owner@example.test" not in raw and "S3cret!" not in raw
    assert store.get(row["id"])["status"] == "FILLED"


def test_browser_requests_then_fills_owner_values_without_rendering_them(tmp_path, monkeypatch):
    store = OwnerInputStore(tmp_path / "requests.json", Vault())
    mgr = Manager()
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'local-route.db'}")
    async def seed_local_agent():
        await db.create_all()
        async with db.session() as session:
            await session.execute(providers.insert().values(id=1, name="local", kind="openai_compat",
                                                          base_url="http://127.0.0.1:11434/v1"))
            await session.execute(models.insert().values(id=1, provider_id=1, name="local-model",
                                                       alias="local-model", kind="local"))
            await session.execute(agents.insert().values(id=1, name="local-agent", model_id=1))
            await session.commit()
    asyncio.run(seed_local_agent())
    svc = SimpleNamespace(owner_input=store, bus=Bus(), db=db)
    ctx = SimpleNamespace(svc=svc, task={"id": 11})
    async def session_for(_ctx, args): return int(args.get("session_id") or 4)
    monkeypatch.setattr(tools_browser, "_session_for", session_for)
    monkeypatch.setattr(tools_browser, "_mgr", lambda _svc: mgr)

    with pytest.raises(PermissionError, match="explicitly bound local agent"):
        asyncio.run(tools_browser._request_owner_fields({"session_id": 4}, ctx))
    ctx.task["agent_id"] = 1
    req = asyncio.run(tools_browser._request_owner_fields({
        "session_id": 4, "context": "signup",
        "success": {"url_changed": True},
        "fields": [{"key": "name", "label": "Name", "ref": "e1-0"},
                   {"key": "password", "label": "Password", "ref": "e1-1", "secret": True}],
    }, ctx))
    assert not req.error and req.data["needs_owner_input"] is True
    rid = req.data["request_id"]
    store.answer(rid, {"name": "Tim", "password": "private-value"}, actor="tg:owner")

    filled = asyncio.run(tools_browser._fill_owner_fields({"request_id": rid}, ctx))
    assert not filled.error and filled.data["filled"] == ["name", "password"]
    assert "Tim" not in filled.render() and "private-value" not in filled.render()
    assert mgr.calls == [("text", 4, "", "e1-0", "Tim"),
                         ("secret", 4, "", "e1-1", "private-value")]
    assert store.get(rid)["status"] == "FILLED"
    asyncio.run(db.close())


class Core:
    def __init__(self): self.answer = None
    async def owner_inputs(self):
        return [{"id": "abcdef123456", "context": "signup",
                 "fields": [{"key": "email", "label": "Email", "secret": False}]}]
    async def answer_owner_input(self, rid, values, actor):
        self.answer = (rid, values, actor)
        return {"id": rid, "status": "ANSWERED",
                "fields": [{"key": "email", "label": "Email"}]}


class Console(ConsoleMixin):
    def __init__(self):
        self.settings = Settings((Person(1, 1, role="owner"),), pc_control=True)
        self.core = Core()
        self.store = SimpleNamespace(put=lambda *_args: None)
    def button(self, person, label, command): return (label, command)


def test_telegram_owner_can_answer_form_data_without_submitting_form():
    app = Console()
    owner = app.settings.people[0]
    listing = asyncio.run(app.console_inputs(owner))
    assert "abcdef123456" in listing and "Email" in listing
    reply = asyncio.run(app.console_input(owner, 'abcdef123456 email=me@example.test'))
    assert "Данные приняты" in reply
    rid, values, actor = app.core.answer
    assert rid == "abcdef123456" and values == {"email": "me@example.test"}
    assert actor == "tg:user:1@chat:1"
    assert "submit/login/ToS не подтверждены" in reply
