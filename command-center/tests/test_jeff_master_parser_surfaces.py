"""Master Parser surfaces: Bossman button API (owner token) and the пульт /parse (owner only)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from bcc.pit.config import config_path, save_setup
from bcc.pit.master_parser import engine
from bcc.pit.master_parser.engine import resolve_settings
from bcc.telegram_companion.config import Person, Settings
from bcc.telegram_companion.parse_bridge import PARSE_DENIED
from bcc.telegram_companion.service import Companion
from bcc.telegram_companion.store import Store

from .conftest import wait_for
from .test_pit_master_parser import ALICE, BOB, FakeModel, build_root, facts_of

OWNER = Person(11111, 11111, "owner", None)
GUEST = Person(22222, 22222, "guest", None)


@pytest.fixture(autouse=True)
def _no_private_blocklist(tmp_path, monkeypatch):
    """Tests never read the owner's private Jeff blocklist."""
    monkeypatch.setenv("BOSSMAN_JEFF_BLOCKLIST", str(tmp_path / "no-blocklist.txt"))


def configure_jeff(data_dir: Path) -> str:
    config = config_path(data_dir)
    config.parent.mkdir(parents=True, exist_ok=True)
    save_setup(config, people=[Person(ALICE, ALICE, "owner")], chat_models=["free/m:free"],
               provider_base_url="https://openrouter.ai/api/v1", core_url="http://127.0.0.1:8800",
               web_only=True, local_url="http://127.0.0.1:11434/v1", local_models=["x"])
    return resolve_settings(config).identity_salt


@pytest.fixture
def fake_local_model(monkeypatch):
    model = FakeModel()
    monkeypatch.setattr(engine.ModelRoute, "local", lambda self: model)
    return model


# -- Bossman window button ------------------------------------------------------------------
async def test_api_requires_the_owner_token(env):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app),
                                 base_url="http://test") as anonymous:
        assert (await anonymous.get("/api/pit/master-parse")).status_code == 401
        assert (await anonymous.post("/api/pit/master-parse", json={})).status_code == 401


async def test_api_refuses_without_jeff_config(env):
    state = (await env.client.get("/api/pit/master-parse")).json()
    assert state["configured"] is False and state["running"] is False
    response = await env.client.post("/api/pit/master-parse", json={})
    assert response.status_code == 409


async def test_button_runs_in_background_and_shows_the_result(env, fake_local_model):
    data_dir = Path(env.settings.data_dir)
    salt = configure_jeff(data_dir)
    data = build_root(data_dir, salt)
    response = await env.client.post("/api/pit/master-parse", json={})
    assert response.status_code == 202

    async def finished():
        state = (await env.client.get("/api/pit/master-parse")).json()
        return state if (not state["running"] and state["report"]) else None

    state = await wait_for(finished, timeout=120)
    assert state["status"]["state"] == "done"
    assert state["report"]["totals"]["facts_added"] >= 3
    report = (await env.client.get("/api/pit/master-parse/report")).json()
    assert report["run_id"] == state["report"]["run_id"]
    values = {f["value"] for f in facts_of(data["vault"], data["keys"][BOB])}
    assert "делает игру про космос" in values
    assert (await env.client.get("/api/pit/master-parse/report?run_id=..%2F..%2Fx")).status_code == 404


# -- пульт /parse ---------------------------------------------------------------------------
class FakeTelegram:
    def __init__(self):
        self.sent = []

    async def send(self, person, text, keyboard=None):
        self.sent.append((person, str(text)))

    async def call(self, *a, **k):
        return True


def companion(tmp_path: Path, data_dir: Path) -> Companion:
    settings = Settings((OWNER, GUEST), local_url="http://127.0.0.1:8083/v1", local_model="best",
                        bot_token="x")
    c = Companion(settings, Store(tmp_path / "pult"), FakeTelegram(), None, None)
    c.parse_data_dir = lambda: data_dir
    return c


def msg(p, text):
    return {"from": {"id": p.user_id, "is_bot": False}, "chat": {"id": p.chat_id, "type": "private"},
            "text": text}


def test_guest_gets_nothing_from_parse(tmp_path, fake_local_model):
    data_dir = tmp_path / "data"
    build_root(data_dir, configure_jeff(data_dir))
    c = companion(tmp_path, data_dir)
    for text in ("/parse", "/parse status", "/parse dry"):
        assert asyncio.run(c.handle(GUEST, msg(GUEST, text))) == PARSE_DENIED
    assert c.parse_job is None and not fake_local_model.prompts
    assert not (engine.parser_home(data_dir) / "corpus.sqlite3").exists()


def test_owner_parse_runs_and_reports_only_to_the_owner(tmp_path, fake_local_model):
    data_dir = tmp_path / "data"
    data = build_root(data_dir, configure_jeff(data_dir))
    c = companion(tmp_path, data_dir)

    async def scenario():
        started = await c.handle(OWNER, msg(OWNER, "/parse"))
        await c.parse_job
        status = await c.handle(OWNER, msg(OWNER, "/parse status"))
        return str(started), str(status)

    started, status = asyncio.run(scenario())
    assert "запущен" in started and "завершён" in status
    assert [person for person, _ in c.telegram.sent] == [OWNER]
    assert "Фактов добавлено" in c.telegram.sent[0][1]
    assert facts_of(data["vault"], data["keys"][ALICE])


def test_owner_revoked_mid_run_gets_no_delivery(tmp_path, fake_local_model):
    data_dir = tmp_path / "data"
    build_root(data_dir, configure_jeff(data_dir))
    c = companion(tmp_path, data_dir)
    revoked = Settings((Person(33333, 33333, "owner", None),), local_url="http://127.0.0.1:8083/v1",
                       local_model="best", bot_token="x")
    original = c.parse_run

    def run_and_revoke(settings, options):
        c.policy_provider = lambda: revoked
        return original(settings, options)

    c.parse_run = run_and_revoke

    async def scenario():
        await c.handle(OWNER, msg(OWNER, "/parse"))
        await c.parse_job

    asyncio.run(scenario())
    assert c.telegram.sent == []
