"""Telegram companion store: one home per Bossman instance, a lossless migration, hardened saves.

Why this file exists (2026-10-01 audit): the settings API read and wrote
``%LOCALAPPDATA%\\Bossman\\telegram-companion`` whatever ``BCC_DATA_DIR`` was, so a
test or release-candidate instance could rewrite the owner's real bot configuration,
and a save erased the owner's executor. Everything here runs in tmp dirs; the
autouse guard in conftest.py pins BOSSMAN_TELEGRAM_CONFIG / BCC_DATA_DIR into
the test's tmp dir, and LOCALAPPDATA is redirected where a legacy home is needed.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import os
import sqlite3
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from bcc.config import Settings
from bcc.features import telegram_settings as ts
from bcc.secrets import Vault
from bcc.telegram_companion import paths
from bcc.telegram_companion.store import Store, single_instance

from .conftest import client_for, start_app
from .test_telegram_settings import FAST, FAST_ID, OSS, OSS_ID, TOKEN, body, models_transport

OWNER, GUEST = 11111, 22222
OWNER_KEY, GUEST_KEY = f"{OWNER}:{OWNER}", f"{GUEST}:{GUEST}"
NOW = dt.datetime(2026, 10, 1, 12, 0, 0, tzinfo=dt.timezone.utc)


# ---------------------------------------------------------------- helpers

def tree(root: Path, *, skip=()) -> dict[str, str]:
    """{relative path: sha256} of every file under ``root``."""
    out = {}
    for current, _dirs, names in os.walk(root):
        for name in names:
            file = Path(current) / name
            rel = file.relative_to(root).as_posix()
            if rel not in skip:
                out[rel] = hashlib.sha256(file.read_bytes()).hexdigest()
    return out


def sqlite_dump(path: Path) -> dict[str, list]:
    """Row content of every table; sqlite files differ byte-wise after a backup-API copy, rows must not."""
    db = sqlite3.connect(path)
    try:
        names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {n: sorted(map(repr, db.execute(f'SELECT * FROM "{n}"'))) for n in names}
    finally:
        db.close()


def profile_count(home: Path) -> int:
    db = sqlite3.connect(home / "companion.sqlite3")
    try:
        return db.execute("SELECT count(*) FROM profiles").fetchone()[0]
    finally:
        db.close()


def make_legacy(local: Path, *, agent_id: int | None = 1) -> Path:
    """A realistic legacy home: key, credentials, config, sealed sqlite store, extras."""
    home = local / "Bossman" / "telegram-companion"
    home.mkdir(parents=True)
    vault = Vault(home)                                           # secret.key
    (home / "credentials.enc").write_text(
        vault.encrypt(json.dumps({"bot_token": TOKEN, "core_token": "", "cloud_token": ""})), encoding="utf-8")
    (home / "config.json").write_text(json.dumps({
        "people": [{"user_id": OWNER, "chat_id": OWNER, "role": "owner", "agent_id": agent_id},
                   {"user_id": GUEST, "chat_id": GUEST, "role": "guest", "agent_id": None}],
        "local_url": OSS, "local_model": OSS_ID, "core_url": "http://127.0.0.1:8801",
        "claude_bridge": True, "pc_control": True, "jev_enabled": True,
    }, indent=2) + "\n", encoding="utf-8")
    store = Store(home)
    try:
        for text in ("- первый", "- второй", "- третий владельца"):
            store.put_profile(OWNER_KEY, text, 0, edited_by_owner=True)    # version 3
        store.put_profile(GUEST_KEY, "- гость", 0)
        store.log(OWNER_KEY, "вопрос", "ответ")
    finally:
        store.close()
    budget = sqlite3.connect(home / "cloud-budget.sqlite3")
    budget.execute("CREATE TABLE spend(day TEXT, usd REAL)")
    budget.execute("INSERT INTO spend VALUES ('2026-09-30', 0.0)")
    budget.commit()
    budget.close()
    (home / "exports").mkdir()
    (home / "exports" / "user-11111.jsonl").write_text('{"x": 1}\n', encoding="utf-8")
    (home / "config.json.bak-20260922").write_text("{}", encoding="utf-8")
    return home


@pytest.fixture
def local(tmp_path, monkeypatch):
    """A fake %LOCALAPPDATA%; the real one is never consulted."""
    base = tmp_path / "fake_local"
    base.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(base))
    monkeypatch.delenv("BOSSMAN_VAULT_KEY", raising=False)
    return base


@pytest.fixture
def by_data_dir(monkeypatch):
    """No explicit path override: the canonical resolver decides, exactly as in production."""
    monkeypatch.delenv("BOSSMAN_TELEGRAM_CONFIG", raising=False)
    monkeypatch.delenv("BOSSMAN_COMPANION_CONFIG", raising=False)
    monkeypatch.setattr(ts, "MODELS_TRANSPORT", models_transport())
    monkeypatch.setattr(ts, "TELEGRAM_TRANSPORT", httpx.MockTransport(
        lambda r: pytest.fail("Telegram must not be called")))
    monkeypatch.setattr(ts, "_PROC", {"proc": None, "started": None, "log": None})
    ts._MIGRATION.clear()
    yield
    ts._MIGRATION.clear()


@pytest.fixture
def pinned(tmp_path, monkeypatch):
    """The test-style pin: BOSSMAN_TELEGRAM_CONFIG inside tmp_path."""
    path = tmp_path / "pinned" / "telegram-companion" / "config.json"
    monkeypatch.setenv("BOSSMAN_TELEGRAM_CONFIG", str(path))
    monkeypatch.setattr(ts, "MODELS_TRANSPORT", models_transport())
    monkeypatch.setattr(ts, "TELEGRAM_TRANSPORT", httpx.MockTransport(
        lambda r: pytest.fail("Telegram must not be called")))
    monkeypatch.setattr(ts, "_PROC", {"proc": None, "started": None, "log": None})
    return path


@asynccontextmanager
async def instance(data_dir: Path):
    """One more Bossman instance with its own data dir (own DB, own token, own app)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(data_dir=data_dir, database_url=f"sqlite+aiosqlite:///{data_dir / 'bcc.db'}",
                        ui_dir=data_dir.parent / "no-ui")
    app, svc = await start_app(settings, start_workers=False)
    try:
        async with client_for(app, svc) as client:
            yield SimpleNamespace(client=client, svc=svc, settings=settings, app=app)
    finally:
        await svc.stop()


def seed_profile(home: Path, key: str = OWNER_KEY, text: str = "- мой профиль", *, edited=True, versions=1) -> dict:
    store = Store(home)
    try:
        value = {}
        for _ in range(versions):
            value = store.put_profile(key, text, 0, edited_by_owner=edited)
        return value
    finally:
        store.close()


# ---------------------------------------------------------------- 1-3: where the store lives

def test_config_path_follows_bcc_data_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("BOSSMAN_TELEGRAM_CONFIG", raising=False)
    monkeypatch.delenv("BOSSMAN_COMPANION_CONFIG", raising=False)
    monkeypatch.setenv("BCC_DATA_DIR", str(tmp_path / "a"))
    assert paths.companion_config_path() == tmp_path / "a" / "telegram-companion" / "config.json"
    from bcc.telegram_companion.__main__ import default_config
    assert default_config() == paths.companion_config_path()
    assert paths.companion_config_path(tmp_path / "b") == tmp_path / "b" / "telegram-companion" / "config.json"
    # explicit overrides win, each in its own priority order
    monkeypatch.setenv("BOSSMAN_TELEGRAM_CONFIG", str(tmp_path / "t.json"))
    monkeypatch.setenv("BOSSMAN_COMPANION_CONFIG", str(tmp_path / "c.json"))
    assert paths.companion_config_path() == tmp_path / "t.json"
    from bcc.pit.bot_guard import companion_config_path as jeff_view
    assert jeff_view() == tmp_path / "c.json"                     # Jeff keeps its historical variable first


async def test_two_data_dirs_are_isolated(tmp_path, local, by_data_dir):
    async with instance(tmp_path / "A" / "data") as a, instance(tmp_path / "B" / "data") as b:
        assert (await a.client.put("/api/telegram/settings", json=body())).status_code == 200
        a_cfg = tmp_path / "A" / "data" / "telegram-companion" / "config.json"
        before = a_cfg.read_bytes()
        got = (await b.client.get("/api/telegram/settings")).json()
        assert got["configured"] is False and got["token_set"] is False
        assert got["config_path"] == str(tmp_path / "B" / "data" / "telegram-companion" / "config.json")
        assert (await b.client.put("/api/telegram/settings", json=body(owner_id=33333, guest_ids=[]))).status_code == 200
        assert a_cfg.read_bytes() == before                            # B's save never reached A
        assert json.loads(a_cfg.read_text(encoding="utf-8"))["core_data_dir"] == str((tmp_path / "A" / "data").resolve())
        assert (await a.client.get("/api/telegram/settings")).json()["owner_id"] == OWNER


async def test_settings_api_never_touches_localappdata(tmp_path, local, by_data_dir):
    sentinel = make_legacy(local)
    before = tree(local)
    async with instance(tmp_path / "elsewhere" / "data") as app:
        c = app.client
        assert (await c.put("/api/telegram/settings", json=body())).status_code == 200
        assert (await c.get("/api/telegram/settings")).status_code == 200
        seed_profile(tmp_path / "elsewhere" / "data" / "telegram-companion", GUEST_KEY, edited=False)
        assert (await c.get("/api/telegram/people")).status_code == 200
        assert (await c.put(f"/api/telegram/profile/{GUEST}", json={"text": "- x"})).status_code in (200, 503)
        assert (await c.delete(f"/api/telegram/profile/{GUEST}")).status_code == 200
        assert (await c.post("/api/telegram/token", json={"bot_token": "987654321:" + "Qz" * 18})).status_code == 200
        assert (await c.delete("/api/telegram/token")).status_code == 200
        assert (await c.post("/api/telegram/export")).status_code == 200
        assert (await c.get("/api/telegram/status")).status_code == 200
    assert tree(local) == before                                      # byte-identical, nothing new appeared
    assert sentinel.is_dir() and not (sentinel / "MIGRATED_TO.json").exists()


async def test_other_instance_never_inherits_the_owner_legacy_home(tmp_path, local, by_data_dir):
    legacy = make_legacy(local)
    before = tree(legacy)
    async with instance(tmp_path / "bugtest" / "data") as app:
        got = (await app.client.get("/api/telegram/settings")).json()
    assert got["configured"] is False and "migration_pending" not in got
    assert not (tmp_path / "bugtest" / "data" / "telegram-companion" / "config.json").exists()
    assert tree(legacy) == before
    assert paths.legacy_applies(tmp_path / "bugtest" / "data") is False
    assert paths.legacy_applies(local / "Bossman" / "CommandCenter") is True
    # read-only callers fall back to the legacy file only for the owner's own instance
    owner_dd, other_dd = local / "Bossman" / "CommandCenter", tmp_path / "bugtest" / "data"
    os.environ.pop("BOSSMAN_TELEGRAM_CONFIG", None)
    os.environ.pop("BOSSMAN_COMPANION_CONFIG", None)
    assert paths.companion_config_path(owner_dd, read_fallback=True) == legacy / "config.json"
    assert paths.companion_config_path(owner_dd) == owner_dd / "telegram-companion" / "config.json"
    assert paths.companion_config_path(other_dd, read_fallback=True) == other_dd / "telegram-companion" / "config.json"


# ---------------------------------------------------------------- 4-8: the migration

def test_migration_backs_up_then_copies_and_keeps_source(tmp_path, local, monkeypatch):
    legacy = make_legacy(local)
    new = local / "Bossman" / "CommandCenter" / "telegram-companion"
    before = tree(legacy)
    owner_profile = Store(legacy).profile(OWNER_KEY)
    events = []
    real = paths._copy_sqlite

    def watching(source, target):
        kind = "backup" if ".migration-backup-" in str(target) else "tmp" if ".tmp-" in str(target) else "other"
        events.append((kind, Path(source).name, any(p.name.startswith("telegram-companion.migration-backup-")
                                                    for p in legacy.parent.iterdir())))
        return real(source, target)
    monkeypatch.setattr(paths, "_copy_sqlite", watching)

    result = paths.migrate_companion_home(legacy, new, now=NOW, owner_data_dir=local / "Bossman" / "CommandCenter")
    assert result["status"] == paths.STATUS_MIGRATED and result["ok"], result
    # backup of EVERY sqlite file strictly before the first byte of the destination
    assert [e[0] for e in events] == ["backup", "backup", "tmp", "tmp"], events
    assert all(e[2] for e in events)                                  # the backup folder already existed each time
    backup = Path(result["backup"])
    assert backup.name == "telegram-companion.migration-backup-20261001T120000Z" and backup.parent == legacy.parent

    after = tree(legacy, skip={"MIGRATED_TO.json"})
    assert after == before                                            # source untouched: same files, same sha256
    assert tree(backup, skip={"companion.sqlite3", "cloud-budget.sqlite3"}) == \
        {k: v for k, v in before.items() if k not in {"companion.sqlite3", "cloud-budget.sqlite3"}}
    assert sqlite_dump(backup / "companion.sqlite3") == sqlite_dump(legacy / "companion.sqlite3")

    # destination: same secret key (first), same sealed rows, credentials readable with the copied key
    assert (new / "secret.key").read_bytes() == (legacy / "secret.key").read_bytes()
    assert (new / "credentials.enc").read_bytes() == (legacy / "credentials.enc").read_bytes()
    assert sqlite_dump(new / "companion.sqlite3") == sqlite_dump(legacy / "companion.sqlite3")
    assert sqlite_dump(new / "cloud-budget.sqlite3") == sqlite_dump(legacy / "cloud-budget.sqlite3")
    store = Store(new)
    try:
        assert store.profile(OWNER_KEY) == owner_profile
    finally:
        store.close()
    assert (new / "exports" / "user-11111.jsonl").is_file() and (new / "config.json.bak-20260922").is_file()
    from bcc.telegram_companion.config import load
    loaded = load(new / "config.json")
    assert loaded.bot_token == TOKEN and next(p for p in loaded.people if p.role == "owner").agent_id == 1
    assert loaded.core_data_dir == str((local / "Bossman" / "CommandCenter").resolve())   # pinned in the COPY only
    assert json.loads((legacy / "config.json").read_text(encoding="utf-8")).get("core_data_dir") is None

    marker = json.loads((legacy / "MIGRATED_TO.json").read_text(encoding="utf-8"))
    assert marker["to"] == str(new) and marker["backup"] == str(backup) and "secret.key" in marker["sha256"]
    assert paths.follow_marker(legacy / "config.json") == new / "config.json"
    assert not [p for p in new.parent.iterdir() if ".tmp-" in p.name]


def test_migration_is_idempotent(tmp_path, local):
    legacy = make_legacy(local)
    new = local / "Bossman" / "CommandCenter" / "telegram-companion"
    first = paths.migrate_companion_home(legacy, new, now=NOW)
    assert first["status"] == paths.STATUS_MIGRATED
    (new / "config.json").write_text(json.dumps({"edited": "after migration"}), encoding="utf-8")
    snapshot = tree(new)
    legacy_snapshot = tree(legacy)
    backups = sorted(p.name for p in legacy.parent.iterdir())
    second = paths.migrate_companion_home(legacy, new, now=NOW + dt.timedelta(minutes=5))
    assert second["status"] == paths.STATUS_ALREADY and second["ok"]
    assert tree(new) == snapshot and tree(legacy) == legacy_snapshot  # nothing overwritten, nothing rewritten
    assert sorted(p.name for p in legacy.parent.iterdir()) == backups  # and no second backup folder
    assert paths.migrate_companion_home(legacy, legacy)["status"] == paths.STATUS_SAME_HOME
    assert paths.migrate_companion_home(local / "nothing", local / "elsewhere")["status"] == paths.STATUS_NO_LEGACY


@pytest.mark.parametrize("fail_on, status", [(1, paths.STATUS_BACKUP_FAILED), (3, paths.STATUS_COPY_FAILED)])
def test_migration_failure_leaves_source_and_destination_clean(tmp_path, local, monkeypatch, fail_on, status):
    legacy = make_legacy(local)
    new = local / "Bossman" / "CommandCenter" / "telegram-companion"
    before = tree(legacy)
    real, calls = paths._copy_sqlite, []

    def flaky(source, target):
        calls.append(target)
        if len(calls) == fail_on:
            raise sqlite3.OperationalError("disk I/O error (simulated)")
        return real(source, target)
    monkeypatch.setattr(paths, "_copy_sqlite", flaky)
    result = paths.migrate_companion_home(legacy, new, now=NOW)
    assert result["status"] == status and result["ok"] is False
    assert tree(legacy) == before and not (legacy / "MIGRATED_TO.json").exists()
    assert not new.exists()
    assert not [p for p in new.parent.iterdir() if ".tmp-" in p.name] if new.parent.exists() else True
    backups = [p for p in legacy.parent.iterdir() if ".migration-backup-" in p.name]
    assert len(backups) == (0 if status == paths.STATUS_BACKUP_FAILED else 1)   # a bad backup is never kept as if good
    # the failure is not sticky: the same call succeeds once the disk behaves
    monkeypatch.setattr(paths, "_copy_sqlite", real)
    assert paths.migrate_companion_home(legacy, new, now=NOW + dt.timedelta(minutes=1))["status"] == paths.STATUS_MIGRATED


def test_migration_refuses_live_poller(tmp_path, local):
    legacy = make_legacy(local)
    new = local / "Bossman" / "CommandCenter" / "telegram-companion"
    before = tree(legacy)
    with single_instance(legacy):
        result = paths.migrate_companion_home(legacy, new, now=NOW)
        assert result["status"] == paths.STATUS_IN_USE and result["ok"] is False
    assert not new.exists() and not (legacy / "MIGRATED_TO.json").exists()
    assert not [p for p in legacy.parent.iterdir() if ".migration-backup-" in p.name]
    assert {k: v for k, v in tree(legacy).items() if k not in {"poller.lock", "poller.json"}} == \
        {k: v for k, v in before.items() if k not in {"poller.lock", "poller.json"}}


def test_migration_requires_secret_key(tmp_path, local):
    legacy = make_legacy(local)
    (legacy / "secret.key").unlink()
    new = local / "Bossman" / "CommandCenter" / "telegram-companion"
    result = paths.migrate_companion_home(legacy, new, now=NOW)
    assert result["status"] == paths.STATUS_NO_KEY and result["ok"] is False
    assert not new.exists() and not [p for p in legacy.parent.iterdir() if ".migration-backup-" in p.name]


def test_migration_parks_a_half_started_destination_instead_of_deleting_it(tmp_path, local):
    legacy = make_legacy(local)
    new = local / "Bossman" / "CommandCenter" / "telegram-companion"
    new.mkdir(parents=True)
    (new / "secret.key").write_bytes(b"stray-key-from-a-half-started-instance")
    assert paths.migrate_companion_home(legacy, new, now=NOW)["status"] == paths.STATUS_MIGRATED
    parked = new.with_name("telegram-companion.pre-migration-20261001T120000Z")
    assert (parked / "secret.key").read_bytes() == b"stray-key-from-a-half-started-instance"
    assert (new / "secret.key").read_bytes() == (legacy / "secret.key").read_bytes()


# ---------------------------------------------------------------- 9: the required proof

async def test_owner_profile_survives_migration_and_repeated_saves(tmp_path, local, by_data_dir):
    owner_dd = local / "Bossman" / "CommandCenter"
    legacy = make_legacy(local)
    new = owner_dd / "telegram-companion"
    legacy_before = tree(legacy)
    legacy_profile = Store(legacy).profile(OWNER_KEY)
    assert legacy_profile["version"] == 3 and legacy_profile["edited_by_owner"] is True

    def owner_state():
        store = Store(new)
        try:
            row = store.profile(OWNER_KEY)
        finally:
            store.close()
        cfg = json.loads((new / "config.json").read_text(encoding="utf-8"))
        owner = next(p for p in cfg["people"] if p["role"] == "owner")
        return row, owner

    def check(step):
        row, owner = owner_state()
        assert row is not None, step
        assert (row["text"], row["version"], row["edited_by_owner"]) == \
            (legacy_profile["text"], 3, True), step
        assert row == legacy_profile, step
        assert owner["user_id"] == OWNER and owner["agent_id"] == 1, step
        count = profile_count(new)
        assert count >= counts[-1], (step, count, counts)
        counts.append(count)
        # the legacy source never changes (only the add-only marker appears)
        assert tree(legacy, skip={"MIGRATED_TO.json"}) == legacy_before, step

    counts = [profile_count(legacy)]
    assert counts == [2]
    first = paths.migrate_companion_home(legacy, new, now=NOW, owner_data_dir=owner_dd)
    assert first["status"] == paths.STATUS_MIGRATED and first["profiles_after"]["companion.sqlite3"] == 2
    check("after migration")

    variants = [
        dict(bot_token="", enabled=True, image_enabled=False),
        dict(bot_token=TOKEN, enabled=False, image_enabled=True),
        dict(bot_token="", enabled=True, image_enabled=True, fastest_url=FAST, fastest_model=FAST_ID,
             default_route="fastest"),
        dict(bot_token=TOKEN, enabled=False, image_enabled=False, guest_ids=[]),
        dict(bot_token="", enabled=True, image_enabled=True, guest_ids=[GUEST], persona="Говори кратко, по делу и без лишних слов."),
    ]
    async with instance(owner_dd) as app:
        for i, variant in enumerate(variants, 1):
            r = await app.client.put("/api/telegram/settings", json=body(**{"owner_id": OWNER, "guest_ids": [GUEST], **variant}))
            assert r.status_code == 200, (i, r.text)
            check(f"after save {i}")
            people = (await app.client.get("/api/telegram/people")).json()["items"]
            assert people[0]["role"] == "owner" and people[0]["profile"] == legacy_profile, i
        second = paths.migrate_companion_home(legacy, new, now=NOW + dt.timedelta(hours=1), owner_data_dir=owner_dd)
        assert second["status"] == paths.STATUS_ALREADY
        check("after second migration")
        people = (await app.client.get("/api/telegram/people")).json()["items"]
        owner = next(p for p in people if p["role"] == "owner")
        assert owner["user_id"] == OWNER and owner["profile"] == legacy_profile
    assert len(counts) == 1 + 1 + len(variants) + 1 and min(counts) == 2


async def test_owner_instance_migrates_lazily_on_first_request(tmp_path, local, by_data_dir):
    owner_dd = local / "Bossman" / "CommandCenter"
    legacy = make_legacy(local)
    before = tree(legacy)
    async with instance(owner_dd) as app:
        got = (await app.client.get("/api/telegram/settings")).json()
    assert got["configured"] is True and got["owner_id"] == OWNER
    assert got["migration"]["status"] == paths.STATUS_MIGRATED
    assert (owner_dd / "telegram-companion" / "config.json").is_file()
    assert tree(legacy, skip={"MIGRATED_TO.json"}) == before


async def test_save_is_refused_while_the_legacy_companion_still_runs(tmp_path, local, by_data_dir):
    owner_dd = local / "Bossman" / "CommandCenter"
    legacy = make_legacy(local)
    before = tree(legacy)
    async with instance(owner_dd) as app:
        with single_instance(legacy):
            got = (await app.client.get("/api/telegram/settings")).json()
            assert got["configured"] is False and got["migration_pending"] is True
            assert got["migration"]["status"] == paths.STATUS_IN_USE
            r = await app.client.put("/api/telegram/settings", json=body())
            assert r.status_code == 409 and "TELEGRAM_MIGRATION_PENDING" in r.text
    assert not (owner_dd / "telegram-companion" / "config.json").exists()
    assert {k: v for k, v in tree(legacy).items() if k not in {"poller.lock", "poller.json"}} == \
        {k: v for k, v in before.items() if k not in {"poller.lock", "poller.json"}}


# ---------------------------------------------------------------- 10-15: saves and deletes

def write_existing(pinned: Path, *, core_token: str = "", **extra) -> Path:
    home = pinned.parent
    home.mkdir(parents=True, exist_ok=True)
    (home / "credentials.enc").write_text(Vault(home).encrypt(json.dumps(
        {"bot_token": TOKEN, "core_token": core_token, "cloud_token": "cloud-keep"})), encoding="utf-8")
    cfg = {"people": [{"user_id": OWNER, "chat_id": OWNER, "role": "owner", "agent_id": 1},
                      {"user_id": GUEST, "chat_id": GUEST, "role": "guest", "agent_id": 2}],
           "local_url": OSS, "local_model": OSS_ID, "claude_bridge": True, "pc_control": True,
           "jev_enabled": True, "monitor_seconds": 77, "profile_every": 13, **extra}
    pinned.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return home


async def test_put_settings_preserves_agent_id_and_unknown_keys(env, pinned):
    write_existing(pinned)
    r = await env.client.put("/api/telegram/settings", json=body(owner_id=OWNER, guest_ids=[GUEST]))
    assert r.status_code == 200, r.text
    cfg = json.loads(pinned.read_text(encoding="utf-8"))
    assert [(p["user_id"], p["role"], p["agent_id"]) for p in cfg["people"]] == [(OWNER, "owner", 1), (GUEST, "guest", 2)]
    assert cfg["claude_bridge"] is True and cfg["pc_control"] is True and cfg["jev_enabled"] is True
    assert cfg["monitor_seconds"] == 77 and cfg["profile_every"] == 13
    # a different Telegram id never inherits the executor of the previous owner
    r = await env.client.put("/api/telegram/settings", json=body(owner_id=33333, guest_ids=[GUEST]))
    cfg = json.loads(pinned.read_text(encoding="utf-8"))
    assert r.status_code == 200 and cfg["people"][0]["agent_id"] is None and cfg["people"][1]["agent_id"] == 2
    # clearing is explicit
    r = await env.client.put("/api/telegram/settings", json=body(owner_id=33333, guest_ids=[GUEST], clear_agent_ids=True))
    assert r.status_code == 200
    assert all(p["agent_id"] is None for p in json.loads(pinned.read_text(encoding="utf-8"))["people"])


async def test_put_settings_never_blanks_tokens_it_does_not_own(env, pinned):
    home = write_existing(pinned, core_token="core-keep", image_enabled=False)

    def stored():
        return json.loads(Vault(home).decrypt((home / "credentials.enc").read_text(encoding="utf-8")))
    assert (await env.client.put("/api/telegram/settings", json=body())).status_code == 200
    assert stored()["core_token"] == "core-keep" and stored()["cloud_token"] == "cloud-keep"
    assert (await env.client.put("/api/telegram/settings", json=body(image_enabled=True))).status_code == 200
    assert stored()["core_token"] == env.svc.auth.token and stored()["cloud_token"] == "cloud-keep"
    assert (await env.client.put("/api/telegram/settings", json=body(image_enabled=False))).status_code == 200
    assert stored()["core_token"] == "" and stored()["cloud_token"] == "cloud-keep"   # the feature was turned off HERE


async def test_put_settings_refuses_corrupt_config_and_backs_up(env, pinned):
    # a valid config is backed up before its first rewrite
    first = await env.client.put("/api/telegram/settings", json=body())
    assert first.status_code == 200
    assert not list(pinned.parent.glob("config.json.bak-*"))          # nothing to back up yet
    original = pinned.read_bytes()
    assert (await env.client.put("/api/telegram/settings", json=body(max_tokens=512))).status_code == 200
    backups = list(pinned.parent.glob("config.json.bak-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert list(pinned.parent.glob("credentials.enc.bak-*"))

    # a corrupt config is NOT replaced by a fresh one
    pinned.write_bytes(b"{not json")
    refused = await env.client.put("/api/telegram/settings", json=body())
    assert refused.status_code == 409 and pinned.read_bytes() == b"{not json"
    # ...unless the owner says so explicitly; then the corrupt bytes are kept beside it
    ok = await env.client.put("/api/telegram/settings", json=body(replace_corrupt_config=True))
    assert ok.status_code == 200, ok.text
    assert any("Повреждённый" in w for w in ok.json()["warnings"])
    assert any(b.read_bytes() == b"{not json" for b in pinned.parent.glob("config.json.bak-*"))
    assert json.loads(pinned.read_text(encoding="utf-8"))["people"][0]["user_id"] == OWNER


async def test_backups_are_pruned_to_the_last_ten(env, pinned):
    for i in range(14):
        assert (await env.client.put("/api/telegram/settings", json=body(max_tokens=64 + i))).status_code == 200
    assert len(list(pinned.parent.glob("config.json.bak-*"))) == ts.BACKUPS_KEPT
    assert len(list(pinned.parent.glob("credentials.enc.bak-*"))) == ts.BACKUPS_KEPT


async def test_put_settings_does_not_repoint_foreign_instance(env, pinned):
    other = str(pinned.parent / "another-instance")
    write_existing(pinned, core_data_dir=other, core_url="http://127.0.0.1:8801")
    before = tree(pinned.parent)
    r = await env.client.put("/api/telegram/settings", json=body())
    assert r.status_code == 409 and "TELEGRAM_OWNED_BY_OTHER_INSTANCE" in r.text
    assert tree(pinned.parent) == before                              # not one byte, no backup either
    ok = await env.client.put("/api/telegram/settings", json=body(take_over_instance=True))
    assert ok.status_code == 200
    assert json.loads(pinned.read_text(encoding="utf-8"))["core_data_dir"] == str(Path(env.settings.data_dir).resolve())


async def test_owner_id_change_warns_and_keeps_old_profile(env, pinned):
    assert (await env.client.put("/api/telegram/settings", json=body())).status_code == 200
    before = seed_profile(pinned.parent, OWNER_KEY, "- профиль прежнего владельца")
    r = await env.client.put("/api/telegram/settings", json=body(owner_id=33333, guest_ids=[GUEST]))
    assert r.status_code == 200
    assert any(OWNER_KEY in w for w in r.json()["warnings"])
    store = Store(pinned.parent)
    try:
        assert store.profile(OWNER_KEY) == before                     # not deleted, not rewritten
    finally:
        store.close()
    assert (await env.client.get("/api/telegram/people")).json()["items"][0]["user_id"] == 33333


async def test_delete_owner_profile_requires_confirm_and_snapshots(env, pinned):
    assert (await env.client.put("/api/telegram/settings", json=body())).status_code == 200
    original = seed_profile(pinned.parent, OWNER_KEY, "- профиль владельца", versions=2)
    refused = await env.client.delete(f"/api/telegram/profile/{OWNER}")
    assert refused.status_code == 409 and "OWNER_PROFILE_DELETE_NEEDS_CONFIRM" in refused.text
    wrong = await env.client.delete(f"/api/telegram/profile/{OWNER}?confirm=yes")
    assert wrong.status_code == 409
    assert profile_count(pinned.parent) == 1
    done = await env.client.delete(f"/api/telegram/profile/{OWNER}?confirm=owner")
    assert done.status_code == 200 and done.json() == {"ok": True}
    store = Store(pinned.parent)
    try:
        assert store.profile(OWNER_KEY) is None and store.profile_backup_count(OWNER_KEY) == 1
        assert store.restore_profile(OWNER_KEY) == original           # the snapshot is restorable, sealed row intact
        assert store.profile(OWNER_KEY) == original
        # a guest needs no confirmation but is snapshotted as well
        seed_profile(pinned.parent, GUEST_KEY, "- гость", edited=False)
    finally:
        store.close()
    assert (await env.client.delete(f"/api/telegram/profile/{GUEST}")).status_code == 200
    store = Store(pinned.parent)
    try:
        assert store.profile(GUEST_KEY) is None and store.profile_backup_count(GUEST_KEY) == 1
        store.forget(GUEST_KEY)                                       # the privacy command keeps nothing
        assert store.profile_backup_count(GUEST_KEY) == 0
    finally:
        store.close()


def test_refresh_profiles_does_not_clobber_owner_edit(tmp_path):
    from bcc.telegram_companion.adapters import Models, Telegram
    from bcc.telegram_companion.config import Person, Settings as CompanionSettings
    from bcc.telegram_companion.service import Companion
    owner = Person(OWNER, OWNER, "owner", None)

    def model(request):
        payload = json.loads(request.content)
        return httpx.Response(200, json={"model": "best", "choices": [
            {"message": {"content": "- свежий профиль модели"}, "finish_reason": "stop"}]})
    settings = CompanionSettings((owner,), local_url=OSS, local_model="best", bot_token="bot-fixture", profile_every=3)
    transport = httpx.MockTransport(model)
    store = Store(tmp_path / "home")
    tg, models = Telegram(settings, transport=transport), Models(settings, tmp_path / "home", transport=transport)
    app = Companion(settings, store, tg, None, models)

    async def go():
        try:
            for i in range(3):
                store.log(owner.key, f"вопрос {i}", "ответ")
            edited = store.put_profile(owner.key, "- правка владельца", 0, edited_by_owner=True)
            await app.refresh_profiles()
            assert store.profile(owner.key) == edited                 # a rebuild never replaces the owner's text
            await app.refresh_profiles(force=True)                    # only an explicit force does
            rebuilt = store.profile(owner.key)
            assert "свежий профиль" in rebuilt["text"] and rebuilt["version"] == 2
            assert rebuilt["edited_by_owner"] is False
            # machine-built profiles keep refreshing as before
            for i in range(3):
                store.log(owner.key, f"ещё вопрос {i}", "ещё ответ")
            await app.refresh_profiles()
            assert store.profile(owner.key)["version"] == 3
        finally:
            await tg.close(); await models.close(); store.close()
    asyncio.run(go())


# ---------------------------------------------------------------- 16-17: the launcher and the guard

def test_serve_follows_migration_marker(tmp_path, local, monkeypatch, capsys):
    legacy = make_legacy(local)
    new = local / "Bossman" / "CommandCenter" / "telegram-companion"
    seen = []

    async def fake_serve(path):
        seen.append(path)
    from bcc.telegram_companion import __main__ as cli
    monkeypatch.setattr(cli, "serve", fake_serve)
    # before migration a launcher on the legacy path runs the legacy home, as it always did
    assert cli.main(["--config", str(legacy / "config.json")]) == 0
    assert seen[-1] == legacy / "config.json"
    assert paths.migrate_companion_home(legacy, new, now=NOW)["status"] == paths.STATUS_MIGRATED
    capsys.readouterr()
    assert cli.main(["--config", str(legacy / "config.json")]) == 0
    assert seen[-1] == new / "config.json"                            # no second diverging copy can start
    assert "TELEGRAM_COMPANION=HOME_MIGRATED_FOLLOWING_MARKER" in capsys.readouterr().out
    # with no --config the canonical home of THIS data dir is used (legacy home, when migrated, via the marker)
    monkeypatch.delenv("BOSSMAN_TELEGRAM_CONFIG", raising=False)
    monkeypatch.delenv("BOSSMAN_COMPANION_CONFIG", raising=False)
    monkeypatch.setenv("BCC_DATA_DIR", str(local / "Bossman" / "CommandCenter"))
    assert cli.main([]) == 0 and seen[-1] == new / "config.json"


def test_migrate_cli_copies_into_the_selected_data_dir(tmp_path, local, monkeypatch, capsys):
    legacy = make_legacy(local)
    before = tree(legacy)
    monkeypatch.delenv("BOSSMAN_TELEGRAM_CONFIG", raising=False)
    monkeypatch.delenv("BOSSMAN_COMPANION_CONFIG", raising=False)
    monkeypatch.setenv("BCC_DATA_DIR", str(tmp_path / "chosen"))
    from bcc.telegram_companion import __main__ as cli
    assert cli.main(["--migrate"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == paths.STATUS_MIGRATED and TOKEN not in json.dumps(printed)
    assert (tmp_path / "chosen" / "telegram-companion" / "config.json").is_file()
    assert tree(legacy, skip={"MIGRATED_TO.json"}) == before
    assert cli.main(["--migrate"]) == 0 and json.loads(capsys.readouterr().out)["status"] == paths.STATUS_ALREADY


def test_test_guard_pins_every_telegram_path_inside_tmp_path(tmp_path):
    """Meta-test: the conftest guard is active, so no test reaches the owner's real folder."""
    for name in ("BOSSMAN_TELEGRAM_CONFIG", "BOSSMAN_COMPANION_CONFIG", "BCC_DATA_DIR"):
        value = os.environ.get(name)
        assert value, f"{name} is not pinned by tests/conftest.py"
        assert tmp_path in Path(value).resolve().parents, (name, value)
    assert paths.companion_config_path().is_relative_to(tmp_path)
    from bcc.telegram_companion.__main__ import default_config
    assert default_config().is_relative_to(tmp_path)
    assert "AppData" not in str(default_config()) or str(tmp_path) in str(default_config())
    _ = sys  # the guard is platform independent


def test_guard_refuses_a_real_localappdata_path(tmp_path, monkeypatch):
    """The resolver itself never returns the legacy home unless the caller opted into the read fallback."""
    monkeypatch.delenv("BOSSMAN_TELEGRAM_CONFIG", raising=False)
    monkeypatch.delenv("BOSSMAN_COMPANION_CONFIG", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "L"))
    monkeypatch.setenv("BCC_DATA_DIR", str(tmp_path / "L" / "Bossman" / "CommandCenter"))
    assert paths.companion_config_path() != paths.legacy_config_path()           # no legacy file yet
    legacy = make_legacy(tmp_path / "L")
    assert paths.companion_config_path() == tmp_path / "L" / "Bossman" / "CommandCenter" / "telegram-companion" / "config.json"
    assert paths.companion_config_path(read_fallback=True) == legacy / "config.json"
