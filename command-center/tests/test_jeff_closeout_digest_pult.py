"""Jeff closeout 10.10, gap 2: the weekly digest outbox (insights/pult-outbox.jsonl) is READ and delivered to the OWNER.

Before: deliver_digest() appended to the outbox and marked the week done, and nothing ever read the file.
Now: owner-only API lists pending ids, the companion Pult sends each to the owner once and acknowledges it.
Fake Telegram and fake core for the companion; no token is read, nothing leaves the process.
"""
from __future__ import annotations

import asyncio
import calendar
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bcc.pit.j2 import insights as I
from bcc.telegram_companion.config import CompanionError, Person
from bcc.telegram_companion.service import Companion

from .test_jeff_settings_overlay import pit_setup  # noqa: F401 (fixture)

NOW = float(calendar.timegm((2026, 10, 5, 7, 0, 0)))           # Monday 10:00 Moscow
OWNER = Person(11111, 11111, "owner", 7)
GUEST = Person(22222, 22222, "guest", None)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def pit(tmp_path):
    home = tmp_path / "pit-v1.7"
    home.mkdir()
    return home


def collector(pit, now=NOW):
    return I.InsightsCollector(pit, clock=lambda: now)


# ------------------------------------------------------------------------------ outbox reader
def test_outbox_lists_each_digest_once_until_acknowledged(pit):
    c = collector(pit)
    first = run(c.deliver_digest())
    assert first["queued_for_pult"] == f"jd-{first['week']}"
    run(c.deliver_digest())                                          # same week again: not queued twice
    items = c.pending_pult_items()
    assert [i["id"] for i in items] == [f"jd-{first['week']}"]
    assert items[0]["text"].startswith("Jeff · неделя")
    assert c.ack_pult_item(items[0]["id"]) is True
    assert c.ack_pult_item(items[0]["id"]) is True                   # idempotent
    assert c.pending_pult_items() == []
    assert c.ack_pult_item("jd-2099-W01") is False                   # unknown id
    assert c.ack_pult_item("../../etc/passwd") is False


def test_next_week_is_a_new_item_and_an_old_outbox_without_ids_is_delivered_too(pit):
    folder = pit / "insights"
    folder.mkdir(parents=True)
    (folder / "pult-outbox.jsonl").write_text(json.dumps({"at": "2026-09-28T06:00:00Z", "kind": "weekly_digest",
                                                          "week": "2026-W40", "text": "старый формат"},
                                                         ensure_ascii=False) + "\n", encoding="utf-8")
    c = collector(pit)
    run(c.deliver_digest())
    ids = [i["id"] for i in c.pending_pult_items()]
    assert ids == ["jd-2026-W40", f"jd-{c._week_label(NOW)}"]


def test_forced_resend_is_a_separate_item(pit):
    c = collector(pit)
    run(c.deliver_digest())
    c.ack_pult_item(c.pending_pult_items()[0]["id"])
    forced = run(c.deliver_digest(force=True))
    assert forced["queued_for_pult"].startswith(f"jd-{forced['week']}-f")
    assert [i["id"] for i in c.pending_pult_items()] == [forced["queued_for_pult"]]


# ------------------------------------------------------------------------------ companion (owner Pult)
class Store:
    def __init__(self):
        self.kv = {}

    def get(self, key, default=None):
        return self.kv.get(key, default)

    def put(self, key, value):
        self.kv[key] = value


class Telegram:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    async def send(self, person, text, keyboard=None):
        if self.fail:
            raise CompanionError("NETWORK_UNAVAILABLE")
        self.sent.append((person, text))
        return 101


class Core:
    """The backend side, backed by a real InsightsCollector over a temp PIT home."""

    def __init__(self, c, ack_fails=False):
        self.c, self.ack_fails, self.acked = c, ack_fails, []

    async def jeff_digests(self):
        return self.c.pending_pult_items()

    async def ack_jeff_digest(self, rid):
        if self.ack_fails:
            raise CompanionError("NETWORK_UNAVAILABLE")
        assert self.c.ack_pult_item(rid)
        self.acked.append(rid)
        return {"id": rid, "delivered": True}


def companion(core, telegram=None, *, console=True, people=(OWNER, GUEST)):
    settings = SimpleNamespace(people=tuple(people), pc_control=console)
    return Companion(settings, Store(), telegram or Telegram(), core, models=object(), policy_provider=lambda: settings)


def test_owner_gets_the_digest_once_and_nobody_else_does(pit):
    c = collector(pit)
    run(c.deliver_digest())
    core, tg = Core(c), Telegram()
    pult = companion(core, tg)
    run(pult.notify_jeff_digests())
    assert [p for p, _ in tg.sent] == [OWNER]
    assert "Jeff · неделя" in tg.sent[0][1]
    assert core.acked == [f"jd-{c._week_label(NOW)}"]
    run(pult.notify_jeff_digests())
    assert len(tg.sent) == 1                                          # deduplicated


def test_failed_send_stays_in_the_outbox_and_a_lost_ack_never_resends(pit):
    c = collector(pit)
    run(c.deliver_digest())
    core = Core(c)
    pult = companion(core, Telegram(fail=True))
    run(pult.notify_jeff_digests())
    assert core.acked == [] and len(c.pending_pult_items()) == 1
    pult.telegram = Telegram()
    core.ack_fails = True                                             # sent, but the ack is lost
    run(pult.notify_jeff_digests())
    assert len(pult.telegram.sent) == 1 and len(c.pending_pult_items()) == 1
    core.ack_fails = False
    run(pult.notify_jeff_digests())                                   # repeats only the ack
    assert len(pult.telegram.sent) == 1 and c.pending_pult_items() == []


def test_no_owner_console_or_no_owner_means_nothing_is_sent(pit):
    c = collector(pit)
    run(c.deliver_digest())
    for kwargs in ({"console": False}, {"people": (GUEST,)}):
        tg = Telegram()
        run(companion(Core(c), tg, **kwargs).notify_jeff_digests())
        assert tg.sent == []
    assert len(c.pending_pult_items()) == 1


def test_a_text_that_looks_like_a_secret_is_not_sent(pit):
    c = collector(pit)
    folder = pit / "insights"
    folder.mkdir(parents=True)
    (folder / "pult-outbox.jsonl").write_text(json.dumps({"id": "jd-2026-W41", "kind": "weekly_digest",
                                                          "week": "2026-W41", "text": "ключ sk-or-v1-" + "a" * 40}) + "\n",
                                              encoding="utf-8")
    tg = Telegram()
    run(companion(Core(c), tg).notify_jeff_digests())
    assert tg.sent == []


def test_an_older_core_without_the_api_is_ignored(pit):
    class OldCore:
        async def jeff_digests(self):
            raise CompanionError("HTTP_404")
    tg = Telegram()
    run(companion(OldCore(), tg).notify_jeff_digests())
    assert tg.sent == []


# ------------------------------------------------------------------------------ owner-only API
async def test_api_lists_and_acknowledges_the_outbox(env, pit_setup):
    import httpx
    c = env.client
    await c.post("/api/jeff-insights/digest/send")
    items = (await c.get("/api/jeff-insights/pult-outbox")).json()["items"]
    assert len(items) == 1 and items[0]["id"].startswith("jd-") and "неделя" in items[0]["text"].lower()
    ok = await c.post(f"/api/jeff-insights/pult-outbox/{items[0]['id']}/delivered", json={})
    assert ok.status_code == 200 and ok.json() == {"id": items[0]["id"], "delivered": True}
    assert (await c.get("/api/jeff-insights/pult-outbox")).json()["items"] == []
    assert (await c.post("/api/jeff-insights/pult-outbox/jd-2099-W01/delivered", json={})).status_code == 404
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url="http://test") as anon:
        assert (await anon.get("/api/jeff-insights/pult-outbox")).status_code == 401
        assert (await anon.post(f"/api/jeff-insights/pult-outbox/{items[0]['id']}/delivered", json={})).status_code == 401


def test_companion_adapter_validates_ids():
    from bcc.telegram_companion.adapters import JEFF_DIGEST_ID
    assert JEFF_DIGEST_ID.fullmatch("jd-2026-W41") and JEFF_DIGEST_ID.fullmatch("jd-2026-W41-f1759640000")
    assert not JEFF_DIGEST_ID.fullmatch("jd-2026-W41/../x") and not JEFF_DIGEST_ID.fullmatch("ar-0123456789ab")
    assert Path(I.__file__).read_text(encoding="utf-8").count('OUTBOX_ID = re.compile(r"jd-\\d{4}-W\\d{2}(?:-f\\d{1,12})?")') == 1
