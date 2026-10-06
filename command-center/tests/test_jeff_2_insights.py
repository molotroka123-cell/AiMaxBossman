"""Jeff 2.0 owner insights: participants table from existing stores, narratives (paths only in lists, text only in
the owner endpoint), health, trends, the weekly digest for the Pult, the owner-only API and the UI page wiring.
Fakes only; synthetic identities; injected clock."""
from __future__ import annotations

import asyncio
import calendar
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from bcc.pit.j2 import insights as I
from bcc.pit.j2.contract import BaseModule, TurnContext
from bcc.pit.j2.pipeline import J2Pipeline
from bcc.pit.j2.proactive import ProactiveStore
from bcc.pit.j2.quality_lab import QualityStore, Scored, Turn
from bcc.pit.models import ConsentState
from bcc.pit.tasks import TaskStore
from bcc.pit.vault import PersonaVault

from .test_jeff_settings_overlay import SALT, TG_A, TG_B, pit_setup  # noqa: F401 (fixture reuse)

PK_A = "a" * 64
PK_B = "b" * 64
NARR_A = "Тайный текст нарратива про участника А"
NOW = float(calendar.timegm((2026, 9, 29, 12, 0, 0)))        # Tuesday
DAY = 86400.0


def run(coro):
    return asyncio.run(coro)


def iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")


def touch(path: Path, ts: float) -> None:
    os.utime(path, (ts, ts))


@pytest.fixture
def pit(tmp_path):
    home = tmp_path / "pit-v1.7"
    home.mkdir()
    return home


def seed_participant(pit: Path, key: str, *, facts=3, raw=5, active_ts=NOW - DAY, memory=True):
    base = pit / "personalities" / key
    (base / "raw").mkdir(parents=True, exist_ok=True)
    (base / "consent.json").write_text(json.dumps(ConsentState(memory_enabled=memory, personalization_enabled=True).to_dict()), "utf-8")
    write_jsonl(base / "facts.jsonl", [{"id": f"f{i}", "value": "секретный факт"} for i in range(facts)])
    write_jsonl(base / "raw" / "events.jsonl", [{"text": "секретное сообщение", "role": "user"} for _ in range(raw)])
    touch(base / "raw" / "events.jsonl", active_ts)


def seed_narrative(pit: Path, key: str):
    folder = pit / "passport-checkpoints" / "narratives"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{key}.json").write_text(json.dumps({
        "schema": "x", "status": "OK", "person_key": key, "run_id": "run1", "model": "qwen-fake",
        "created_at": "2026-09-27T10:00:00Z",
        "paragraphs": {"context": NARR_A, "personality": "Похоже, участник любит порядок."}}, ensure_ascii=False), "utf-8")


def seed_route_log(pit: Path):
    rows = []
    for offset, ok, lat in ((1, True, 1000), (2, True, 2000), (3, True, 3000), (3, False, 9000),
                            (9, True, 4000), (10, True, 2000)):
        rows.append({"at": iso(NOW - offset * DAY), "ok": ok, "latency_ms": lat, "model": "m"})
    write_jsonl(pit / "logs" / "route_log.jsonl", rows)


def seed_heartbeat(pit: Path, *, age=10, state="running", where=""):
    folder = pit / where if where else pit
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "heartbeat.json").write_text(json.dumps({
        "schema": "bossman.pit.heartbeat/1", "surface": "telegram", "state": state, "at_epoch": int(NOW - age),
        "replies_ok": 40, "replies_failed": 2, "avg_latency_ms": 2100, "last_reply_latency_ms": 1800,
        "jeff_version": "2.0-test"}), "utf-8")


def seed_queue(pit: Path, pending: int):
    db = sqlite3.connect(pit / "companion.sqlite3")
    db.execute("CREATE TABLE inbox (id INTEGER PRIMARY KEY, phase TEXT)")
    db.executemany("INSERT INTO inbox (phase) VALUES (?)", [("pending",)] * pending + [("done",)] * 3)
    db.commit()
    db.close()


def collector(pit: Path) -> I.InsightsCollector:
    return I.InsightsCollector(pit, clock=lambda: NOW)


# -- participants table ----------------------------------------------------------------------------------------
def test_empty_state_everything_is_empty_not_an_error(pit):
    c = collector(pit)
    assert c.participants() == [] and c.narratives() == []
    overview = c.overview()
    assert overview["participants"] == [] and overview["health"]["heartbeat"]["telegram"]["availability"] == "absent"
    assert overview["trends"]["series"]["replies"] == [None] * len(overview["trends"]["days"])


def test_participants_table_reads_existing_stores(pit):
    seed_participant(pit, PK_A, facts=3, raw=5)
    seed_participant(pit, PK_B, facts=1, raw=2, memory=False, active_ts=NOW - 20 * DAY)
    rows = {r["key"]: r for r in collector(pit).participants(labels={PK_A: "alice"})}
    a, b = rows[PK_A], rows[PK_B]
    assert a["label"] == "alice" and b["label"] == "Участник bbbbbb"
    assert (a["facts"], a["messages"]) == (3, 5) and (b["facts"], b["messages"]) == (1, 2)
    assert a["consent"]["memory"] is True and b["consent"]["memory"] is False
    assert a["active_7d"] is True and b["active_7d"] is False
    assert a["last_activity"] == iso(NOW - DAY)


def test_participants_include_tasks_reminders_quality_and_narrative_flags(pit):
    seed_participant(pit, PK_A)
    seed_narrative(pit, PK_A)
    ts = TaskStore(pit)
    created = ts.create(PK_A, "Подготовить план", [{"id": "s1", "needs_input": True}])
    run(ts.run(PK_A, created["id"], {}))
    ProactiveStore(pit / "personalities").add_item(PK_A, "reminder", "секретное дело", NOW + 60, "k1")
    turn = Turn(user="q", reply="a")
    QualityStore(pit / "personalities").record(PK_A, Scored(dims={}, overall=0.8, flags=[]), turn, now=NOW - DAY)
    row = collector(pit).participants()[0]
    assert row["tasks"] == {"WAITING_INPUT": 1}
    assert row["reminders_pending"] == 1
    assert row["quality"] == {"n": 1, "overall": 0.8}
    assert row["narrative"] is True


def test_participants_table_contains_no_stored_text(pit):
    seed_participant(pit, PK_A)
    seed_narrative(pit, PK_A)
    blob = json.dumps(collector(pit).participants(), ensure_ascii=False)
    assert "секретн" not in blob and NARR_A not in blob


def test_participants_ignore_invalid_directories(pit):
    seed_participant(pit, PK_A)
    (pit / "personalities" / "not-a-key").mkdir()
    (pit / "personalities" / "readme.txt").write_text("x", "utf-8")
    assert [r["key"] for r in collector(pit).participants()] == [PK_A]


def test_participant_rows_are_capped(pit):
    for n in range(I.MAX_PARTICIPANTS + 5):
        (pit / "personalities" / f"{n:064x}").mkdir(parents=True)
    assert len(collector(pit).participants()) == I.MAX_PARTICIPANTS


# -- narratives -------------------------------------------------------------------------------------------------
def test_narrative_list_has_paths_only(pit):
    seed_narrative(pit, PK_A)
    rows = collector(pit).narratives(labels={PK_A: "alice"})
    assert rows[0]["path"] == f"passport-checkpoints/narratives/{PK_A}.json" and rows[0]["label"] == "alice"
    assert rows[0]["model"] == "qwen-fake" and rows[0]["created_at"] == "2026-09-27T10:00:00Z"
    blob = json.dumps(rows, ensure_ascii=False)
    assert NARR_A not in blob and "paragraphs" not in blob and str(pit) not in blob


def test_narrative_text_is_available_to_the_owner_function_only(pit):
    seed_narrative(pit, PK_A)
    text = collector(pit).narrative_text(PK_A)
    assert text["context"] == NARR_A and "порядок" in text["personality"]
    assert collector(pit).narrative_text(PK_B) is None
    with pytest.raises(ValueError):
        collector(pit).narrative_text("../x")


def test_corrupt_narrative_files_are_skipped(pit):
    seed_narrative(pit, PK_A)
    (pit / "passport-checkpoints" / "narratives" / f"{PK_B}.json").write_text("{bad", "utf-8")
    assert [r["person_key"] for r in collector(pit).narratives()] == [PK_A]


# -- health -----------------------------------------------------------------------------------------------------
def test_health_reads_heartbeat_queue_tasks_and_recent_errors(pit):
    seed_heartbeat(pit, age=10)
    seed_heartbeat(pit, age=500, where="web")
    seed_queue(pit, pending=2)
    write_jsonl(pit / "logs" / "runtime_error.jsonl", [{"at": "x", "kind": "Timeout", "detail": "секретный текст"},
                                                      {"at": "y", "kind": "Timeout"}, {"at": "z", "kind": "Boom"}])
    health = collector(pit).health()
    tg, win = health["heartbeat"]["telegram"], health["heartbeat"]["window"]
    assert tg["availability"] == "up" and tg["age_s"] == 10 and tg["replies_ok"] == 40
    assert win["availability"] == "stale"
    assert health["queue"] == 2
    assert health["recent_errors"] == {"count": 3, "kinds": {"Timeout": 2, "Boom": 1}}
    assert "секретный" not in json.dumps(health, ensure_ascii=False)
    assert health["ok"] is True


def test_health_stopped_and_stale_heartbeats_are_not_ok(pit):
    seed_heartbeat(pit, age=900)
    assert collector(pit).health()["ok"] is False
    seed_heartbeat(pit, age=5, state="stopped")
    hb = collector(pit).health()
    assert hb["heartbeat"]["telegram"]["availability"] == "stopped" and hb["ok"] is False


def test_health_queue_unreadable_is_minus_one(pit):
    (pit / "companion.sqlite3").write_text("not a database", "utf-8")
    assert collector(pit).health()["queue"] == -1


def test_model_guard_status_comes_from_the_module_snapshot_if_present(pit):
    assert collector(pit).health()["model_guard"] == {"present": False}
    I.write_status_snapshot(pit, [
        {"name": "model_guard", "version": "1", "order": 20, "breaker_open": False,
         "calls": {"ok": 5, "error": 0, "timeout": 0, "skipped": 0}, "state": "degraded", "restarts": 1,
         "nested": {"a": 1}, "long": "x" * 200},
        {"name": "proactive", "version": "1", "order": 80, "breaker_open": True,
         "calls": {"ok": 1, "error": 3, "timeout": 0, "skipped": 0}}], NOW - 30)
    health = collector(pit).health()
    guard = health["model_guard"]
    assert guard["present"] is True and guard["status"]["state"] == "degraded" and guard["status"]["restarts"] == 1
    assert "nested" not in guard["status"] and "long" not in guard["status"]
    assert health["snapshot_age_s"] == 30
    assert {m["name"]: m["breaker_open"] for m in health["modules"]} == {"model_guard": False, "proactive": True}
    assert health["ok"] is False                              # no Telegram heartbeat in this fixture
    assert guard["healthy"] is False


# -- trends -----------------------------------------------------------------------------------------------------
def test_trends_are_daily_series_computed_from_logs(pit):
    seed_route_log(pit)
    turn = Turn(user="q", reply="a")
    store = QualityStore(pit / "personalities")
    for ts, overall in ((NOW - DAY, 0.9), (NOW - DAY, 0.7), (NOW - 3 * DAY, 0.5)):
        store.record(PK_A, Scored(dims={}, overall=overall, flags=[]), turn, now=ts)
    write_jsonl(pit / "tasks" / PK_A / "events.jsonl", [
        {"at": iso(NOW - 2 * DAY), "task": "t1", "action": "state", "state": "DONE"},
        {"at": iso(NOW - 2 * DAY), "task": "t2", "action": "state", "state": "FAILED"},
        {"at": iso(NOW - 2 * DAY), "task": "t3", "action": "create", "state": "PLANNED"}])
    trends = collector(pit).trends(days=14)
    days = trends["days"]
    assert len(days) == 14 and days[-1] == iso(NOW)[:10] and days[0] == iso(NOW - 13 * DAY)[:10]
    s = trends["series"]

    def at(name, offset):
        return s[name][days.index(iso(NOW - offset * DAY)[:10])]
    assert at("replies", 3) == 2 and at("ok_rate", 3) == 0.5 and at("avg_latency_ms", 3) == 3000
    assert at("replies", 1) == 1 and at("avg_latency_ms", 1) == 1000
    assert at("replies", 5) is None and at("ok_rate", 5) is None
    assert at("quality", 1) == 0.8 and at("quality", 3) == 0.5
    assert at("tasks_done", 2) == 1 and at("tasks_failed", 2) == 1


def test_trends_window_is_clamped(pit):
    assert len(collector(pit).trends(days=0)["days"]) == 1
    assert len(collector(pit).trends(days=9999)["days"]) == I.MAX_TREND_DAYS


def test_trends_skip_malformed_lines(pit):
    path = pit / "logs" / "route_log.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"at":"' + iso(NOW - DAY) + '","ok":true,"latency_ms":500}\n{broken\n[1,2]\n{"at":"bad"}\n', "utf-8")
    trends = collector(pit).trends(days=3)
    assert trends["series"]["replies"][-2] == 1


# -- weekly digest ------------------------------------------------------------------------------------------------
def full_seed(pit):
    seed_participant(pit, PK_A, active_ts=NOW - DAY)
    seed_participant(pit, PK_B, active_ts=NOW - 30 * DAY)
    seed_narrative(pit, PK_A)
    seed_route_log(pit)
    seed_heartbeat(pit)
    seed_queue(pit, 1)
    turn = Turn(user="q", reply="a")
    store = QualityStore(pit / "personalities")
    store.record(PK_A, Scored(dims={}, overall=0.8, flags=[]), turn, now=NOW - DAY)
    store.record(PK_A, Scored(dims={}, overall=0.6, flags=[]), turn, now=NOW - 2 * DAY)
    store.record(PK_A, Scored(dims={}, overall=0.4, flags=[]), turn, now=NOW - 10 * DAY)


def test_weekly_digest_numbers_are_computed_not_invented(pit):
    full_seed(pit)
    digest = collector(pit).weekly_digest()
    facts, text = digest["facts"], digest["text"]
    assert facts["participants"] == 2 and facts["active_7d"] == 1
    assert facts["replies"] == {"this": 4, "prev": 2}
    assert facts["ok_rate"] == {"this": 0.75, "prev": 1.0}
    assert facts["avg_latency_ms"] == {"this": 2000, "prev": 3000}
    assert facts["quality"]["this"] == pytest.approx(0.7) and facts["quality"]["prev"] == pytest.approx(0.4)
    assert facts["quality"]["n"] == 2
    assert "Ответов за неделю: 4 (неделей раньше: 2)" in text
    assert "75%" in text and "2,0 с" in text
    iso_week = datetime.fromtimestamp(NOW, timezone.utc).isocalendar()
    assert digest["week"] == f"{iso_week[0]}-W{iso_week[1]:02d}"
    assert digest["week"] in text


def test_weekly_digest_has_no_participant_text_or_keys(pit):
    full_seed(pit)
    text = collector(pit).weekly_digest()["text"]
    for forbidden in ("секретн", NARR_A, PK_A, PK_B, "участник любит порядок"):
        assert forbidden not in text


def test_weekly_digest_flags_problems_for_the_owner(pit):
    seed_participant(pit, PK_A)
    seed_heartbeat(pit, age=900)
    seed_queue(pit, 12)
    text = collector(pit).weekly_digest()["text"]
    assert "Внимание" in text and "heartbeat" in text and "очередь" in text.lower()


def test_weekly_digest_on_empty_data_says_so(pit):
    digest = collector(pit).weekly_digest()
    assert "данных пока нет" in digest["text"].lower()
    assert digest["facts"]["replies"] == {"this": 0, "prev": 0}


def test_weekly_digest_is_deterministic(pit):
    full_seed(pit)
    assert collector(pit).weekly_digest() == collector(pit).weekly_digest()


def test_digest_delivery_goes_to_the_outbox_once_per_week(pit):
    full_seed(pit)
    c = collector(pit)
    first = run(c.deliver_digest())
    second = run(c.deliver_digest())
    assert first["delivered"] is True and second == {**second, "delivered": False, "already": True}
    outbox = (pit / "insights" / "pult-outbox.jsonl").read_text("utf-8").splitlines()
    assert len(outbox) == 1 and json.loads(outbox[0])["kind"] == "weekly_digest"
    assert (pit / "insights" / "weekly" / f"{first['week']}.txt").read_text("utf-8") == c.weekly_digest()["text"] + "\n"
    forced = run(c.deliver_digest(force=True))
    assert forced["delivered"] is True


def test_digest_uses_an_injected_pult_sender_and_retries_after_failure(pit):
    full_seed(pit)
    c = collector(pit)
    got = []

    async def flaky(text):
        got.append(text)
        return len(got) > 1
    assert run(c.deliver_digest(pult_sender=flaky))["delivered"] is False
    assert run(c.deliver_digest(pult_sender=flaky))["delivered"] is True
    assert run(c.deliver_digest(pult_sender=flaky))["already"] is True
    assert len(got) == 2 and got[0] == got[1]


def test_digest_sender_exception_is_not_delivery(pit):
    full_seed(pit)

    async def boom(text):
        raise RuntimeError("pult down")
    result = run(collector(pit).deliver_digest(pult_sender=boom))
    assert result["delivered"] is False and result["error"] == "RuntimeError"


# -- module -----------------------------------------------------------------------------------------------------
class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def module(pit, *, clock=None, provider=None, sender=None):
    clock = clock or Clock(NOW)
    c = I.InsightsCollector(pit, clock=clock)
    return I.InsightsModule(c, status_provider=provider, pult_sender=sender, clock=clock, tz_offset_min=0), c, clock


def test_module_metadata():
    assert I.InsightsModule.name == "insights" and I.InsightsModule.order == 95


def test_tick_writes_a_status_snapshot_from_the_pipeline(pit):
    class Probe(BaseModule):
        name, order = "probe", 40

        def status(self):
            return {"name": "probe", "version": "1", "level": 3, "secret": {"nested": "x"}}
    pipeline = J2Pipeline([Probe()])
    mod, c, _ = module(pit, provider=pipeline.status)
    run(mod.tick())
    snap = json.loads((pit / "j2-status.json").read_text("utf-8"))
    assert snap["at"] == NOW and snap["modules"][0]["name"] == "probe" and snap["modules"][0]["level"] == 3
    assert "nested" not in json.dumps(snap)


def test_tick_sends_the_weekly_digest_on_monday_morning_once(pit):
    full_seed(pit)
    sent = []

    async def sender(text):
        sent.append(text)
        return True
    monday = float(calendar.timegm((2026, 10, 5, 10, 0, 0)))
    clock = Clock(monday - 2 * DAY)                       # Saturday: not yet
    mod, c, clock = module(pit, clock=clock, sender=sender)
    run(mod.tick())
    assert sent == []
    clock.t = float(calendar.timegm((2026, 10, 5, 7, 0, 0)))      # Monday, before 09:00
    run(mod.tick())
    assert sent == []
    clock.t = monday
    run(mod.tick())
    run(mod.tick())
    assert len(sent) == 1
    assert mod.status()["last_digest_week"] is not None


def test_a_failed_digest_is_retried_on_the_next_tick(pit):
    full_seed(pit)
    results = [False, True]
    sent = []

    async def sender(text):
        sent.append(text)
        return results.pop(0)
    clock = Clock(float(calendar.timegm((2026, 10, 5, 10, 0, 0))))
    mod, *_ = module(pit, clock=clock, sender=sender)
    run(mod.tick())
    run(mod.tick())
    run(mod.tick())
    assert len(sent) == 2


def test_module_never_touches_turns(pit):
    mod, *_ = module(pit)
    ctx = TurnContext(person_key=PK_A, who="tg:1", text="привет")
    assert run(mod.pre_route(ctx)) is None and run(mod.augment(ctx)) is None and run(mod.post_reply(ctx, "ок")) is None


def test_module_status_has_no_text_and_loop_lifecycle(pit):
    async def scenario():
        mod, c, _ = module(pit)
        ticked = asyncio.Event()
        original = mod.tick

        async def counting():
            await original()
            ticked.set()
        mod.tick = counting

        async def fake_sleep(_):
            await asyncio.sleep(3600)
        mod._sleep = fake_sleep
        await mod.start()
        await asyncio.wait_for(ticked.wait(), 2)
        assert mod.status()["running"] is True
        await mod.stop()
        return mod.status()
    status = run(scenario())
    assert status["running"] is False and status["name"] == "insights" and status["errors"] == 0


def test_a_failing_tick_is_counted_not_raised(pit):
    class BadProvider:
        def __call__(self):
            raise RuntimeError("boom")
    mod, *_ = module(pit, provider=BadProvider())
    run(mod.tick())
    assert mod.status()["errors"] == 1


def test_create_builds_from_a_runtime_like_object(tmp_path):
    class Vault:
        root = tmp_path / "pit-v1.7" / "personalities"
        data_dir = tmp_path

    class Runtime:
        vault = Vault()
        home = tmp_path / "pit-v1.7"
    mod = I.create(Runtime())
    assert mod.name == "insights"


# -- owner-only API ------------------------------------------------------------------------------------------------
ROUTES = ("/api/jeff-insights/overview", "/api/jeff-insights/participants", "/api/jeff-insights/narratives",
          "/api/jeff-insights/narratives/" + "a" * 64, "/api/jeff-insights/trends", "/api/jeff-insights/digest")


async def test_api_is_owner_only(env, pit_setup):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url="http://test") as anon:
        for url in ROUTES:
            assert (await anon.get(url)).status_code == 401, url
        assert (await anon.post("/api/jeff-insights/digest/send")).status_code == 401
        anon.cookies.set("jeff_session", "participant-session-token")
        assert (await anon.get("/api/jeff-insights/overview")).status_code == 401


async def test_api_empty_state_is_a_clean_200(env, pit_setup):
    got = await env.client.get("/api/jeff-insights/overview")
    assert got.status_code == 200
    data = got.json()
    assert data["participants"] == [] and data["configured"] is True
    assert data["health"]["heartbeat"]["telegram"]["availability"] == "absent"
    assert (await env.client.get("/api/jeff-insights/participants")).json()["participants"] == []
    assert (await env.client.get("/api/jeff-insights/narratives")).json()["narratives"] == []


async def test_api_without_a_jeff_setup_is_still_a_clean_200(env):
    got = await env.client.get("/api/jeff-insights/overview")
    assert got.status_code == 200 and got.json()["configured"] is False


async def test_api_overview_uses_owner_labels_and_leaks_no_ids_or_text(env, pit_setup):
    pit_dir = Path(env.settings.data_dir) / "pit-v1.7"
    from bcc.features.jeff_settings import _salt
    vault = PersonaVault(Path(env.settings.data_dir), _salt(Path(env.settings.data_dir)))
    key_a, key_b = vault.key_for_telegram(TG_A), vault.key_for_telegram(TG_B)
    for key in (key_a, key_b):
        seed_participant(pit_dir, key)
    seed_narrative(pit_dir, key_a)
    data = (await env.client.get("/api/jeff-insights/overview")).json()
    labels = {p["key"]: p["label"] for p in data["participants"]}
    assert labels[key_a] == "Telegram · владелец" and labels[key_b] == "Telegram · участник 1"
    blob = json.dumps(data, ensure_ascii=False)
    for forbidden in (str(TG_A), str(TG_B), NARR_A, "секретн", str(pit_dir)):
        assert forbidden not in blob


async def test_api_narrative_text_only_through_the_owner_endpoint(env, pit_setup):
    pit_dir = Path(env.settings.data_dir) / "pit-v1.7"
    seed_narrative(pit_dir, PK_A)
    lst = (await env.client.get("/api/jeff-insights/narratives")).json()["narratives"]
    assert lst[0]["path"].endswith(f"{PK_A}.json") and NARR_A not in json.dumps(lst, ensure_ascii=False)
    one = await env.client.get(f"/api/jeff-insights/narratives/{PK_A}")
    assert one.status_code == 200 and one.json()["context"] == NARR_A
    assert (await env.client.get(f"/api/jeff-insights/narratives/{PK_B}")).status_code == 404
    assert (await env.client.get("/api/jeff-insights/narratives/not-a-key")).status_code == 422


async def test_api_trends_param_is_bounded(env, pit_setup):
    got = (await env.client.get("/api/jeff-insights/trends?days=5")).json()
    assert len(got["days"]) == 5
    assert (await env.client.get("/api/jeff-insights/trends?days=100000")).status_code in (200, 422)


async def test_api_digest_get_and_send_is_idempotent_per_week(env, pit_setup):
    pit_dir = Path(env.settings.data_dir) / "pit-v1.7"
    first = (await env.client.get("/api/jeff-insights/digest")).json()
    assert "неделя" in first["text"].lower() and first["week"] in first["text"]
    sent = (await env.client.post("/api/jeff-insights/digest/send")).json()
    again = (await env.client.post("/api/jeff-insights/digest/send")).json()
    assert sent["delivered"] is True and again["already"] is True
    assert (pit_dir / "insights" / "pult-outbox.jsonl").is_file()


# -- UI wiring (static; the browser sweep runs the page) -------------------------------------------------------
UI = Path(__file__).resolve().parents[1] / "ui" / "pages"


def test_ui_page_is_registered_and_matches_its_manifest():
    index = (UI / "index.js").read_text("utf-8")
    page = (UI / "jeff_insights.js").read_text("utf-8")
    entry = re.search(r"lazyPage\(\{ id: 'jeff-insights'[^}]*\},\s*\(\) => import\('\./jeff_insights\.js'\)", index)
    assert entry, "jeff-insights must be registered in ui/pages/index.js"
    for field in re.findall(r"(\w+): '([^']+)'", entry.group(0).split("},")[0]):
        assert f"{field[0]}: '{field[1]}'" in page, field


def test_ui_page_uses_only_the_owner_api_and_guards_buttons():
    page = (UI / "jeff_insights.js").read_text("utf-8")
    assert "/api/jeff-insights/overview" in page and "/api/jeff-insights/narratives/" in page
    assert "console.log" not in page and "innerHTML" not in page
    assert page.count("disabled") >= 1 and "title:" in page
    assert page.endswith("export default JeffInsightsPage;\n")


# -- UI page in real Chromium (same harness as the other lazy pages) ----------------------------------------------
def _browser_run(tmp_path, seed, check):
    from .browser_support import chromium_available, reason as browser_reason
    if not chromium_available():
        pytest.skip(browser_reason())
    from playwright.sync_api import sync_playwright
    from .test_ux2_thinking_pane import LiveServer, _launch, _login
    srv = LiveServer(tmp_path).start()
    problems: list[str] = []
    try:
        seed(Path(srv.settings.data_dir) / "pit-v1.7")
        with sync_playwright() as pw:
            browser = _launch(pw)
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
                page.on("console", lambda m: problems.append("console: " + m.text) if m.type == "error" else None)
                page.on("response", lambda r: problems.append(f"http {r.status}: {r.url}") if r.status >= 400 else None)
                _login(page, srv)
                page.goto(srv.url + "/#/jeff-insights", wait_until="domcontentloaded")
                page.wait_for_selector("[data-testid=jeff-insights]", timeout=20000)
                check(page, Path(srv.settings.data_dir) / "pit-v1.7")
            finally:
                browser.close()
    finally:
        srv.stop()
    assert not problems, problems


def test_ui_empty_state_has_no_errors_and_no_dead_buttons(tmp_path):
    def check(page, pit_dir):
        assert "Участников пока нет" in page.inner_text("[data-testid=jeff-insights]")
        for button in page.locator("[data-testid=jeff-insights] button").all():
            assert button.get_attribute("title") or button.inner_text().strip(), "unlabelled button"
        page.get_by_role("button", name="Показать дайджест недели").click()
        page.wait_for_selector("pre.bx-code", timeout=10000)
        assert "неделя" in page.inner_text("pre.bx-code").lower()
        page.get_by_role("button", name="Отправить в Пульт").click()
        deadline = 60
        while deadline and not (pit_dir / "insights" / "pult-outbox.jsonl").is_file():
            page.wait_for_timeout(100)
            deadline -= 1
        assert (pit_dir / "insights" / "pult-outbox.jsonl").is_file()
    _browser_run(tmp_path, lambda pit_dir: None, check)


def test_ui_seeded_state_disables_narrative_button_without_narrative_and_opens_it_with_one(tmp_path):
    def seed(pit_dir):
        seed_participant(pit_dir, PK_A)
        seed_participant(pit_dir, PK_B)
        seed_narrative(pit_dir, PK_A)

    def check(page, pit_dir):
        rows = page.locator("[data-testid=jeff-insights] tbody tr")
        assert rows.count() == 2
        buttons = page.get_by_role("button", name="Нарратив")
        states = sorted(buttons.nth(i).is_disabled() for i in range(2))
        assert states == [False, True]
        disabled = [buttons.nth(i) for i in range(2) if buttons.nth(i).is_disabled()][0]
        assert "Master Parser" in disabled.get_attribute("title")
        enabled = [buttons.nth(i) for i in range(2) if not buttons.nth(i).is_disabled()][0]
        enabled.click()
        page.wait_for_selector(".modal", timeout=10000)
        assert NARR_A in page.inner_text(".modal")
    _browser_run(tmp_path, seed, check)
