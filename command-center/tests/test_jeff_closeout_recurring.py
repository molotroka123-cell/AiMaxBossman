"""Jeff closeout 10.10, gap 6: recurring reminders (daily / weekly) and a configurable time zone.

Before: every reminder was one-shot and the default zone was a fixed Moscow (or an env variable). Fakes only.
Parser cases follow a GLM-5.3 Flash draft (its parser was rewritten here; its examples were kept).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from bcc.pit import jeff_settings as js
from bcc.pit.j2 import proactive as P
from bcc.pit.j2.contract import TurnContext

from .test_jeff_2_proactive import PK_A, Clock, FakeSender, fmt, local, make

TZ = 180
NOW = local(2026, 9, 29, 12, 0)          # Tuesday 12:00 Moscow


@pytest.fixture(autouse=True)
def _fresh_overlay_cache(monkeypatch):
    monkeypatch.delenv(js.ENV_PATH, raising=False)
    monkeypatch.delenv("BOSSMAN_JEFF_TZ_MIN", raising=False)
    js._cache.clear()
    js._warned.clear()
    yield
    js._cache.clear()


def run(coro):
    return asyncio.run(coro)


def ctx(text, mid="1", key=PK_A):
    return TurnContext(person_key=key, who="tg:1", text=text, message_id=mid)


# ------------------------------------------------------------------------------ parser
@pytest.mark.parametrize("text,expected", [
    ("напоминай каждый день в 9 пить воду", {"every": "daily", "hour": 9, "minute": 0, "what": "пить воду"}),
    ("напоминай ежедневно в 21:30 принять таблетки", {"every": "daily", "hour": 21, "minute": 30, "what": "принять таблетки"}),
    ("напомни каждое утро размяться", {"every": "daily", "hour": 9, "minute": 0, "what": "размяться"}),
    ("напоминай каждый вечер в 8 гулять с собакой", {"every": "daily", "hour": 20, "minute": 0, "what": "гулять с собакой"}),
    ("напоминай каждый понедельник в 10 сдать отчёт",
     {"every": "weekly", "weekday": 0, "hour": 10, "minute": 0, "what": "сдать отчет"}),
    ("напоминай по пятницам в 18:00 созвон", {"every": "weekly", "weekday": 4, "hour": 18, "minute": 0, "what": "созвон"}),
    ("напоминай каждую среду купить хлеб", {"every": "weekly", "weekday": 2, "hour": 9, "minute": 0, "what": "купить хлеб"}),
    ("напоминай каждую неделю в воскресенье в 12 уборка",
     {"every": "weekly", "weekday": 6, "hour": 12, "minute": 0, "what": "уборка"}),
    ("напомни каждый день в 7:05 зарядка", {"every": "daily", "hour": 7, "minute": 5, "what": "зарядка"}),
    ("Напоминай каждый день пить воду в 8:30", {"every": "daily", "hour": 8, "minute": 30, "what": "пить воду"}),
    ("напоминай каждый день", {"every": "daily", "hour": 9, "minute": 0, "what": ""}),
])
def test_recurrence_parser(text, expected):
    assert P.parse_recurrence(text) == expected


@pytest.mark.parametrize("text", ["напомни завтра в 9 позвонить", "каждый день я гуляю",
                                  "напоминай каждый день в 25 тест", "напомни через 2 часа выпить воду"])
def test_not_recurring(text):
    assert P.parse_recurrence(text) is None


def test_next_occurrence_daily_weekly_and_zone():
    daily = {"every": "daily", "hour": 9, "minute": 0}
    assert fmt(P.next_occurrence(daily, NOW, TZ)) == "2026-09-30 09:00"            # 12:00 passed today
    assert fmt(P.next_occurrence(daily, local(2026, 9, 29, 8, 0), TZ)) == "2026-09-29 09:00"
    friday = {"every": "weekly", "weekday": 4, "hour": 18, "minute": 0}
    assert fmt(P.next_occurrence(friday, NOW, TZ)) == "2026-10-02 18:00"
    tuesday_noon = {"every": "weekly", "weekday": 1, "hour": 12, "minute": 0}
    assert fmt(P.next_occurrence(tuesday_noon, NOW, TZ)) == "2026-10-06 12:00"     # strictly after now
    # the same wall-clock rule in UTC+5 is two hours earlier in absolute time than in Moscow
    assert P.next_occurrence(daily, NOW, 300) == P.next_occurrence(daily, NOW, TZ) - 2 * 3600


# ------------------------------------------------------------------------------ module + engine
def _module(tmp_path, *, sender=None, clock=None, tz_provider=None):
    store, clock, sender, engine = make(tmp_path, sender=sender, clock=clock)
    engine.tz_provider = tz_provider
    return P.ProactiveModule(engine), store, clock, sender, engine


def test_daily_series_fires_every_day_and_survives_restart(tmp_path):
    module, store, clock, sender, engine = _module(tmp_path)
    reply = run(module.pre_route(ctx("напоминай каждый день в 9 пить воду"))).reply
    assert "каждый день в 09:00" in reply and "завтра в 09:00" in reply
    assert run(module.pre_route(ctx("напоминай каждый день в 9 пить воду"))) is not None   # same message id: idempotent
    assert len(store.items(PK_A, states=("pending",))) == 1

    for day in (30, 1, 2):                                       # three mornings, a restart before the third
        month = 9 if day == 30 else 10
        clock.t = local(2026, month, day, 9, 0) + 30
        if day == 2:
            _, _, _, engine = make(tmp_path, sender=sender, clock=clock)              # new process, same files
        run(engine.tick())
    assert [t for _, t, _ in sender.sent] == ["Напоминание: пить воду"] * 3
    pending = store.items(PK_A, states=("pending",))
    assert len(pending) == 1 and fmt(pending[0]["due"]) == "2026-10-03 09:00"
    run(engine.tick())                                           # same minute again: nothing is sent twice
    assert len(sender.sent) == 3


def test_weekly_series_and_listing(tmp_path):
    module, store, clock, sender, engine = _module(tmp_path)
    run(module.pre_route(ctx("напоминай по пятницам в 18:00 созвон")))
    listing = run(module.pre_route(ctx("мои напоминания", mid="2"))).reply
    assert "каждую пятницу в 18:00" in listing and "созвон" in listing
    clock.t = local(2026, 10, 2, 18, 0) + 5
    run(engine.tick())
    assert len(sender.sent) == 1
    assert fmt(store.items(PK_A, states=("pending",))[0]["due"]) == "2026-10-09 18:00"


def test_cancelling_the_occurrence_ends_the_series(tmp_path):
    module, store, clock, sender, engine = _module(tmp_path)
    run(module.pre_route(ctx("напоминай каждый день в 9 пить воду")))
    assert "Отменил напоминание 1" in run(module.pre_route(ctx("отмени напоминание 1", mid="2"))).reply
    for day in (30, 1):
        clock.t = local(2026, 9 if day == 30 else 10, day, 9, 1)
        run(engine.tick())
    assert sender.sent == [] and store.items(PK_A, states=("pending",)) == []


def test_missed_or_late_occurrence_still_plans_the_next_one(tmp_path):
    module, store, clock, sender, engine = _module(tmp_path, sender=FakeSender(["undeliverable"]))
    run(module.pre_route(ctx("напоминай каждый день в 9 пить воду")))
    clock.t = local(2026, 9, 30, 9, 0) + 10
    run(engine.tick())                                           # Jeff window: shown next turn, series goes on
    assert store.items(PK_A, states=("missed",))
    assert fmt(store.items(PK_A, states=("pending",))[0]["due"]) == "2026-10-01 09:00"


def test_owner_default_zone_comes_from_jeff_settings(tmp_path):
    data = tmp_path / "data"
    js.write_overlay(js.settings_path(data), {"version": 1, "tz_offset_min": 300})
    assert js.default_tz_offset_min(data) == 300
    module, store, clock, sender, engine = _module(tmp_path, tz_provider=lambda: js.default_tz_offset_min(data))
    reply = run(module.pre_route(ctx("напоминай каждый день в 9 пить воду"))).reply
    assert "UTC+5" in reply
    due = store.items(PK_A, states=("pending",))[0]["due"]
    assert fmt(due, tz=300) == "2026-09-30 09:00"
    # the participant's own zone still wins for that participant
    assert "UTC+5:30" in run(module.pre_route(ctx("мой часовой пояс UTC+5:30", mid="2"))).reply
    assert engine.tz_for(store.prefs(PK_A)) == 330


def test_default_zone_order_file_then_env_then_moscow(tmp_path, monkeypatch):
    assert js.default_tz_offset_min(tmp_path) == 180
    monkeypatch.setenv("BOSSMAN_JEFF_TZ_MIN", "60")
    assert js.default_tz_offset_min(tmp_path) == 60
    js.write_overlay(js.settings_path(tmp_path), {"version": 1, "tz_offset_min": -300})
    assert js.default_tz_offset_min(tmp_path) == -300
    for bad in (900, 7, "180", True):
        with pytest.raises(js.OverlayError):
            js.normalize({"version": 1, "tz_offset_min": bad})


def test_insights_digest_hour_follows_the_owner_zone(tmp_path):
    from bcc.pit.j2.insights import InsightsCollector, InsightsModule
    monday_0630_utc = datetime(2026, 10, 5, 6, 30, tzinfo=timezone.utc).timestamp()
    module = InsightsModule(InsightsCollector(tmp_path), tz_offset_min=180)
    assert module._digest_due(monday_0630_utc) is True                          # 09:30 Moscow
    module.tz_provider = lambda: 0
    assert module._digest_due(monday_0630_utc) is False                         # 06:30 UTC: not yet


async def test_owner_api_sets_and_clears_the_zone(env, pit_setup):
    c = env.client
    body = {"defaults": {"behavior_scales": {}, "system_extra": ""}}
    assert (await c.get("/api/jeff-settings")).json()["tz_effective_min"] == 180
    r = await c.put("/api/jeff-settings", json={**body, "tz_offset_min": 300})
    assert r.status_code == 200 and r.json()["settings"]["tz_offset_min"] == 300
    assert js.default_tz_offset_min(pit_setup) == 300
    await c.put("/api/jeff-settings", json=body)                                 # not mentioned: kept
    assert js.default_tz_offset_min(pit_setup) == 300
    assert (await c.put("/api/jeff-settings", json={**body, "tz_offset_min": 1000})).status_code == 422
    r = await c.put("/api/jeff-settings", json={**body, "tz_offset_min": None})
    assert "tz_offset_min" not in r.json()["settings"] and js.default_tz_offset_min(pit_setup) == 180


from .test_jeff_settings_overlay import pit_setup  # noqa: E402,F401  (fixture)
