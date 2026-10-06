"""Jeff 2.0 proactive companion: Russian time phrases, reminders, follow-ups, digest, quiet hours,
rate limits, opt-out, consent and idempotent delivery across restarts. Fakes only: injected clock and sender."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bcc.pit.j2 import proactive as P
from bcc.pit.j2.contract import Advice, TurnContext
from bcc.pit.j2.pipeline import J2Pipeline

PK_A = "a" * 64
PK_B = "b" * 64
TZ = 180


def local(y, mo, d, h=0, mi=0, tz=TZ) -> float:
    return datetime(y, mo, d, h, mi, tzinfo=timezone(timedelta(minutes=tz))).timestamp()


def fmt(ts: float, tz=TZ) -> str:
    return datetime.fromtimestamp(ts, timezone(timedelta(minutes=tz))).strftime("%Y-%m-%d %H:%M")


NOW = local(2026, 9, 29, 12, 0)          # Tuesday 12:00 local


class Clock:
    def __init__(self, t: float = NOW):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeSender:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.sent: list[tuple[str, str, str]] = []
        self.calls = 0

    async def __call__(self, person_key: str, text: str, key: str):
        self.calls += 1
        result = self.results.pop(0) if self.results else True
        if isinstance(result, Exception):
            raise result
        if result is True or result == "sent":
            self.sent.append((person_key, text, key))
        return result


class VerifyingSender(FakeSender):
    def __init__(self, verdict, results=None):
        super().__init__(results)
        self.verdict = verdict

    async def already_sent(self, key: str):
        return self.verdict


def make(tmp_path: Path, *, sender=None, clock=None, tasks=None, access=None):
    store = P.ProactiveStore(tmp_path / "personalities")
    clock = clock or Clock()
    sender = sender or FakeSender()
    engine = P.ProactiveEngine(store, clock=clock, sender=sender, tasks=tasks, access=access,
                               default_tz_min=TZ)
    return store, clock, sender, engine


def run(coro):
    return asyncio.run(coro)


# -- Russian time-phrase parser: a labelled test set (now = Tue 2026-09-29 12:00, UTC+3) -------------------
LABELLED = [
    ("напомни завтра в 10", "2026-09-30 10:00", ""),
    ("напомни завтра в 10 позвонить маме", "2026-09-30 10:00", "позвонить маме"),
    ("напомни позвонить маме завтра в 10", "2026-09-30 10:00", "позвонить маме"),
    ("напомни через 2 часа выпить воду", "2026-09-29 14:00", "выпить воду"),
    ("напомни через полчаса", "2026-09-29 12:30", ""),
    ("напомни через 15 минут проверить духовку", "2026-09-29 12:15", "проверить духовку"),
    ("напомни через час", "2026-09-29 13:00", ""),
    ("напомни через полтора часа", "2026-09-29 13:30", ""),
    ("напомни через 90 минут", "2026-09-29 13:30", ""),
    ("напомни через пару часов", "2026-09-29 14:00", ""),
    ("напомни сегодня в 18:30 про встречу", "2026-09-29 18:30", "про встречу"),
    ("напомни в 15:00 принять таблетки", "2026-09-29 15:00", "принять таблетки"),
    ("напомни в 9 утра купить хлеб", "2026-09-30 09:00", "купить хлеб"),
    ("напомни послезавтра в 9 утра", "2026-10-01 09:00", ""),
    ("напомни в пятницу в 10 отчёт", "2026-10-02 10:00", "отчёт"),
    ("напомни во вторник в 10", "2026-10-06 10:00", ""),
    ("напомни в понедельник", "2026-10-05 09:00", ""),
    ("напомни завтра вечером позвонить", "2026-09-30 19:00", "позвонить"),
    ("напомни завтра утром", "2026-09-30 09:00", ""),
    ("напомни 15 октября в 9 оплатить аренду", "2026-10-15 09:00", "оплатить аренду"),
    ("напомни 3 октября", "2026-10-03 09:00", ""),
    ("напомни через 3 дня в 10", "2026-10-02 10:00", ""),
    ("напомни через неделю", "2026-10-06 12:00", ""),
    ("напомни завтра в 10 вечера", "2026-09-30 22:00", ""),
    ("напомни завтра в 5 позвонить", "2026-09-30 17:00", "позвонить"),
    ("напомни в полдень", "2026-09-30 12:00", ""),
    ("напомни завтра в 10:30 встреча", "2026-09-30 10:30", "встреча"),
    ("напомни в 22.15", "2026-09-29 22:15", ""),
    ("напомни завтра в 10 часов утра", "2026-09-30 10:00", ""),
    ("Напомни, пожалуйста, завтра в 8 утра позвонить в банк", "2026-09-30 08:00", "позвонить в банк"),
    ("напомни мне завтра в 11 выключить плиту", "2026-09-30 11:00", "выключить плиту"),
    ("напомни через 5 минут", "2026-09-29 12:05", ""),
    ("напомни 1 января в 0:00 поздравить", "2027-01-01 00:00", "поздравить"),
]


@pytest.mark.parametrize("phrase,expected,what", LABELLED, ids=[c[0][:40] for c in LABELLED])
def test_labelled_russian_time_phrases(phrase, expected, what):
    parsed = P.parse_reminder(phrase, NOW, TZ)
    assert parsed is not None, phrase
    assert fmt(parsed.due) == expected
    assert parsed.what == what


@pytest.mark.parametrize("phrase", [
    "привет как дела", "напомни мне купить молоко", "напомни сегодня в 8 утра",
    "напомни завтра в 25", "расскажи про завтрашний день", "",
])
def test_phrases_that_are_not_reminders_or_are_in_the_past(phrase):
    assert P.parse_reminder(phrase, NOW, TZ) is None


def test_timezone_offset_moves_the_absolute_time():
    a = P.parse_reminder("напомни завтра в 10", NOW, 180)
    b = P.parse_reminder("напомни завтра в 10", NOW, 300)
    assert a.due - b.due == 2 * 3600


def test_when_text_is_human_and_relative_to_now():
    p = P.parse_reminder("напомни завтра в 10 позвонить", NOW, TZ)
    assert p.when_text == "завтра в 10:00"
    q = P.parse_reminder("напомни сегодня в 18:30", NOW, TZ)
    assert q.when_text == "сегодня в 18:30"
    r = P.parse_reminder("напомни 15 октября в 9", NOW, TZ)
    assert r.when_text == "15 октября в 09:00"


def test_quiet_hours_wrap_midnight_and_end_time():
    assert P.in_quiet_hours(local(2026, 9, 29, 23, 30), TZ, 22, 8)
    assert P.in_quiet_hours(local(2026, 9, 30, 3, 0), TZ, 22, 8)
    assert not P.in_quiet_hours(local(2026, 9, 29, 12, 0), TZ, 22, 8)
    assert fmt(P.quiet_end(local(2026, 9, 29, 23, 30), TZ, 22, 8)) == "2026-09-30 08:00"
    assert fmt(P.quiet_end(local(2026, 9, 30, 3, 0), TZ, 22, 8)) == "2026-09-30 08:00"


# -- store: persisted under the participant namespace ------------------------------------------------------
def test_store_lives_in_the_participant_namespace(tmp_path):
    store, *_ = make(tmp_path)
    store.add_item(PK_A, "reminder", "x", NOW + 60, "k1")
    path = tmp_path / "personalities" / PK_A / "proactive" / "schedule.json"
    assert path.is_file()
    assert not (tmp_path / "personalities" / PK_B).exists()


def test_store_rejects_bad_person_keys(tmp_path):
    store, *_ = make(tmp_path)
    with pytest.raises(ValueError):
        store.add_item("../evil", "reminder", "x", NOW, "k")


def test_store_isolates_participants(tmp_path):
    store, *_ = make(tmp_path)
    store.add_item(PK_A, "reminder", "секрет А", NOW + 60, "ka")
    store.add_item(PK_B, "reminder", "тайна Б", NOW + 60, "kb")
    assert [i["text"] for i in store.items(PK_A)] == ["секрет А"]
    assert [i["text"] for i in store.items(PK_B)] == ["тайна Б"]
    assert sorted(store.participants()) == [PK_A, PK_B]


def test_store_survives_reopen_and_dedupes_keys(tmp_path):
    store, *_ = make(tmp_path)
    assert store.add_item(PK_A, "reminder", "x", NOW + 60, "same") is not None
    assert store.add_item(PK_A, "reminder", "x", NOW + 60, "same") is None
    reopened = P.ProactiveStore(tmp_path / "personalities")
    assert len(reopened.items(PK_A)) == 1


def test_store_corrupt_file_falls_back_to_empty(tmp_path):
    store, *_ = make(tmp_path)
    store.add_item(PK_A, "reminder", "x", NOW + 60, "k")
    (tmp_path / "personalities" / PK_A / "proactive" / "schedule.json").write_text("{not json", "utf-8")
    assert store.items(PK_A) == []


def test_prefs_defaults_are_conservative(tmp_path):
    store, *_ = make(tmp_path)
    prefs = store.prefs(PK_A)
    assert prefs["proactive"] is False and prefs["opted_out"] is False and prefs["digest"] is False
    assert (prefs["quiet_start"], prefs["quiet_end"]) == (22, 8)
    assert prefs["max_per_day"] == 3


def test_prefs_are_validated_and_persisted(tmp_path):
    store, *_ = make(tmp_path)
    store.set_prefs(PK_A, quiet_start=23, quiet_end=7, max_per_day=99, tz_offset_min=99999, bogus=1)
    prefs = P.ProactiveStore(tmp_path / "personalities").prefs(PK_A)
    assert (prefs["quiet_start"], prefs["quiet_end"]) == (23, 7)
    assert prefs["max_per_day"] <= P.MAX_PER_DAY_LIMIT
    assert abs(prefs["tz_offset_min"]) <= 14 * 60
    assert "bogus" not in prefs


# -- engine: reminders, idempotency, restart ---------------------------------------------------------------
def test_reminder_is_sent_once_when_due_and_not_before(tmp_path):
    store, clock, sender, engine = make(tmp_path)
    store.add_item(PK_A, "reminder", "позвонить маме", NOW + 600, "r1")
    run(engine.tick())
    assert sender.sent == []
    clock.advance(601)
    run(engine.tick())
    run(engine.tick())
    assert [(pk, t) for pk, t, _ in sender.sent] == [(PK_A, "Напоминание: позвонить маме")]


def test_no_duplicate_after_restart(tmp_path):
    store, clock, sender, engine = make(tmp_path)
    store.add_item(PK_A, "reminder", "x", NOW + 10, "r1")
    clock.advance(20)
    run(engine.tick())
    store2, _, _, engine2 = make(tmp_path, sender=sender, clock=clock)
    run(engine2.tick())
    assert len(sender.sent) == 1


def test_restart_before_due_still_delivers_later(tmp_path):
    store, clock, sender, _ = make(tmp_path)
    store.add_item(PK_A, "reminder", "x", NOW + 3600, "r1")
    _, _, _, engine2 = make(tmp_path, sender=sender, clock=clock)
    clock.advance(3601)
    run(engine2.tick())
    assert len(sender.sent) == 1


def test_sender_gets_the_items_idempotency_key(tmp_path):
    store, clock, sender, engine = make(tmp_path)
    item = store.add_item(PK_A, "reminder", "x", NOW, "the-key")
    run(engine.tick())
    assert sender.sent[0][2] == "the-key" == item["key"]


def test_sender_failure_without_delivery_is_retried_with_backoff(tmp_path):
    sender = FakeSender([False, True])
    store, clock, sender, engine = make(tmp_path, sender=sender)
    store.add_item(PK_A, "reminder", "x", NOW, "r1")
    run(engine.tick())
    assert sender.calls == 1 and sender.sent == []
    run(engine.tick())                       # still inside the backoff window
    assert sender.calls == 1
    clock.advance(P.RETRY_BACKOFF_S * 2 + 1)
    run(engine.tick())
    assert len(sender.sent) == 1


def test_gives_up_after_max_attempts(tmp_path):
    sender = FakeSender([False] * 10)
    store, clock, sender, engine = make(tmp_path, sender=sender)
    store.add_item(PK_A, "reminder", "x", NOW, "r1")
    for _ in range(8):
        run(engine.tick())
        clock.advance(3600)
    assert sender.calls == P.MAX_ATTEMPTS
    assert store.items(PK_A)[0]["state"] == "failed"


def test_ambiguous_send_is_never_repeated_without_proof(tmp_path):
    sender = FakeSender([RuntimeError("timeout after send")])
    store, clock, sender, engine = make(tmp_path, sender=sender)
    store.add_item(PK_A, "reminder", "x", NOW, "r1")
    run(engine.tick())
    clock.advance(7200)
    run(engine.tick())
    assert sender.calls == 1
    assert store.items(PK_A)[0]["state"] == "unknown"


def test_crash_between_send_and_mark_verifier_says_not_sent(tmp_path):
    """State 'sending' persisted, process died: the verifier decides."""
    store, clock, sender, engine = make(tmp_path, sender=VerifyingSender(False))
    item = store.add_item(PK_A, "reminder", "x", NOW, "r1")
    store.update_item(PK_A, item["id"], state="sending", attempts=1)
    run(engine.tick())
    assert len(sender.sent) == 1               # verified NOT sent -> one safe retry


def test_crash_between_send_and_mark_verifier_says_sent(tmp_path):
    store, clock, sender, engine = make(tmp_path, sender=VerifyingSender(True))
    item = store.add_item(PK_A, "reminder", "x", NOW, "r1")
    store.update_item(PK_A, item["id"], state="sending", attempts=1)
    run(engine.tick())
    assert sender.sent == [] and store.items(PK_A)[0]["state"] == "sent"


def test_crash_without_verifier_marks_unknown_and_does_not_resend(tmp_path):
    store, clock, sender, engine = make(tmp_path)
    item = store.add_item(PK_A, "reminder", "x", NOW, "r1")
    store.update_item(PK_A, item["id"], state="sending", attempts=1)
    run(engine.tick())
    assert sender.sent == [] and store.items(PK_A)[0]["state"] == "unknown"


def test_undeliverable_surface_marks_missed_not_lost(tmp_path):
    sender = FakeSender(["undeliverable"])
    store, clock, sender, engine = make(tmp_path, sender=sender)
    store.add_item(PK_A, "reminder", "купить хлеб", NOW, "r1")
    run(engine.tick())
    assert store.items(PK_A)[0]["state"] == "missed"


def test_stale_reminder_is_delivered_with_a_late_note(tmp_path):
    store, clock, sender, engine = make(tmp_path)
    store.add_item(PK_A, "reminder", "оплатить счёт", NOW - 5 * 3600, "r1")
    run(engine.tick())
    assert "опоздало" in sender.sent[0][1] and "оплатить счёт" in sender.sent[0][1]


def test_very_stale_reminder_expires_instead_of_firing(tmp_path):
    store, clock, sender, engine = make(tmp_path)
    store.add_item(PK_A, "reminder", "x", NOW - 3 * 86400, "r1")
    run(engine.tick())
    assert sender.sent == [] and store.items(PK_A)[0]["state"] == "expired"


def test_reminders_ignore_quiet_hours_but_followups_do_not(tmp_path):
    clock = Clock(local(2026, 9, 29, 23, 30))
    store, clock, sender, engine = make(tmp_path, clock=clock)
    store.set_prefs(PK_A, proactive=True)
    store.add_item(PK_A, "reminder", "ночное", clock.t, "r1")
    store.add_item(PK_A, "followup", "как дела?", clock.t, "f1")
    run(engine.tick())
    assert [t for _, t, _ in sender.sent] == ["Напоминание: ночное"]
    item = [i for i in store.items(PK_A) if i["kind"] == "followup"][0]
    assert item["state"] == "pending" and fmt(item["due"]) == "2026-09-30 08:00"


def test_followup_goes_out_after_quiet_hours(tmp_path):
    clock = Clock(local(2026, 9, 29, 23, 30))
    store, clock, sender, engine = make(tmp_path, clock=clock)
    store.set_prefs(PK_A, proactive=True)
    store.add_item(PK_A, "followup", "как дела?", clock.t, "f1")
    run(engine.tick())
    clock.t = local(2026, 9, 30, 8, 5)
    run(engine.tick())
    assert len(sender.sent) == 1 and "как дела?" in sender.sent[0][1]


def test_unsolicited_messages_are_rate_limited_per_day(tmp_path):
    store, clock, sender, engine = make(tmp_path)
    store.set_prefs(PK_A, proactive=True, max_per_day=2, min_gap_s=0)
    for n in range(4):
        store.add_item(PK_A, "followup", f"f{n}", NOW, f"f{n}")
    run(engine.tick())
    assert len(sender.sent) == 2
    run(engine.tick())
    assert len(sender.sent) == 2
    clock.t = local(2026, 9, 30, 12, 0)
    run(engine.tick())
    assert len(sender.sent) == 4


def test_min_gap_between_unsolicited_messages(tmp_path):
    store, clock, sender, engine = make(tmp_path)
    store.set_prefs(PK_A, proactive=True, max_per_day=5, min_gap_s=3600)
    store.add_item(PK_A, "followup", "one", NOW, "f1")
    store.add_item(PK_A, "followup", "two", NOW, "f2")
    run(engine.tick())
    assert len(sender.sent) == 1
    clock.advance(3601)
    run(engine.tick())
    assert len(sender.sent) == 2


def test_rate_limits_are_per_participant(tmp_path):
    store, clock, sender, engine = make(tmp_path)
    for pk in (PK_A, PK_B):
        store.set_prefs(pk, proactive=True, max_per_day=1, min_gap_s=0)
        store.add_item(pk, "followup", "hi", NOW, "f-" + pk[:1])
        store.add_item(pk, "followup", "hi2", NOW, "g-" + pk[:1])
    run(engine.tick())
    assert sorted(pk[:1] for pk, _, _ in sender.sent) == ["a", "b"]


def test_unsolicited_needs_consent(tmp_path):
    store, clock, sender, engine = make(tmp_path)
    store.add_item(PK_A, "followup", "как дела?", NOW, "f1")
    run(engine.tick())
    assert sender.sent == []


def test_unsolicited_carries_the_one_tap_opt_out_hint_but_reminders_do_not(tmp_path):
    store, clock, sender, engine = make(tmp_path)
    store.set_prefs(PK_A, proactive=True)
    store.add_item(PK_A, "followup", "как дела?", NOW, "f1")
    store.add_item(PK_A, "reminder", "позвонить", NOW, "r1")
    run(engine.tick())
    follow = [t for _, t, _ in sender.sent if "как дела?" in t][0]
    remind = [t for _, t, _ in sender.sent if t.startswith("Напоминание")][0]
    assert "стоп напоминания" in follow and "стоп напоминания" not in remind


def test_opt_out_cancels_everything_and_blocks_sends(tmp_path):
    store, clock, sender, engine = make(tmp_path)
    store.set_prefs(PK_A, proactive=True)
    store.add_item(PK_A, "followup", "f", NOW, "f1")
    store.add_item(PK_A, "reminder", "r", NOW, "r1")
    engine.opt_out(PK_A)
    run(engine.tick())
    assert sender.sent == []
    assert {i["state"] for i in store.items(PK_A)} == {"cancelled"}
    assert store.prefs(PK_A)["opted_out"] is True and store.prefs(PK_A)["proactive"] is False


def test_opt_in_restores_consent(tmp_path):
    store, clock, sender, engine = make(tmp_path)
    engine.opt_out(PK_A)
    engine.opt_in(PK_A)
    prefs = store.prefs(PK_A)
    assert prefs["opted_out"] is False and prefs["proactive"] is True


def test_revoked_participant_gets_nothing(tmp_path):
    store, clock, sender, engine = make(tmp_path, access=lambda pk: False)
    store.add_item(PK_A, "reminder", "r", NOW, "r1")
    run(engine.tick())
    assert sender.sent == [] and store.items(PK_A)[0]["state"] == "cancelled"


def test_other_participants_are_unaffected_by_one_optout(tmp_path):
    store, clock, sender, engine = make(tmp_path)
    store.add_item(PK_A, "reminder", "a", NOW, "ra")
    store.add_item(PK_B, "reminder", "b", NOW, "rb")
    engine.opt_out(PK_A)
    run(engine.tick())
    assert [pk[:1] for pk, _, _ in sender.sent] == ["b"]


def test_pending_reminder_cap(tmp_path):
    store, *_ = make(tmp_path)
    for n in range(P.MAX_PENDING + 5):
        store.add_item(PK_A, "reminder", f"x{n}", NOW + 60 + n, f"k{n}")
    assert len(store.items(PK_A, states=("pending",))) == P.MAX_PENDING


# -- follow-ups on long tasks (bcc/pit/tasks.py states) ----------------------------------------------------
def task(state, *, since="2026-09-29T09:00:00Z", goal="Собрать отчёт", tid="1" * 16, updated="2026-09-29T09:00:00Z"):
    return {"id": tid, "state": state, "goal": goal, "updated_at": updated,
            "awaited_input": {"step": "s1", "since": since} if state == "WAITING_INPUT" else None,
            "awaited_approval": "s1" if state == "WAITING_APPROVAL" else None}


def with_tasks(tmp_path, tasks, at):
    store, clock, sender, engine = make(tmp_path, tasks=lambda pk: tasks.get(pk, []),
                                        clock=Clock(P.parse_utc(at)))
    store.set_prefs(PK_A, proactive=True, min_gap_s=0, max_per_day=10, quiet_start=0, quiet_end=0)
    return store, clock, sender, engine


def test_waiting_input_task_gets_one_followup_after_the_delay(tmp_path):
    tasks = {PK_A: [task("WAITING_INPUT")]}
    store, clock, sender, engine = with_tasks(tmp_path, tasks, "2026-09-29T11:30:00Z")
    run(engine.tick())
    assert sender.sent == []
    clock.t = P.parse_utc("2026-09-29T12:10:00Z")
    run(engine.tick())
    assert len(sender.sent) == 1 and "Собрать отчёт" in sender.sent[0][1]
    run(engine.tick())
    assert len(sender.sent) == 1


def test_followup_is_not_repeated_after_restart(tmp_path):
    tasks = {PK_A: [task("WAITING_INPUT")]}
    store, clock, sender, engine = with_tasks(tmp_path, tasks, "2026-09-29T13:00:00Z")
    run(engine.tick())
    _, _, _, engine2 = make(tmp_path, sender=sender, clock=clock, tasks=lambda pk: tasks.get(pk, []))
    run(engine2.tick())
    assert len(sender.sent) == 1


def test_second_followup_after_a_day_then_stop(tmp_path):
    tasks = {PK_A: [task("WAITING_INPUT")]}
    store, clock, sender, engine = with_tasks(tmp_path, tasks, "2026-09-29T13:00:00Z")
    run(engine.tick())
    clock.t = P.parse_utc("2026-09-30T13:00:00Z")
    run(engine.tick())
    clock.t = P.parse_utc("2026-10-02T13:00:00Z")
    run(engine.tick())
    assert len(sender.sent) == 2


def test_new_wait_on_the_same_task_is_a_new_followup(tmp_path):
    box = {PK_A: [task("WAITING_INPUT")]}
    store, clock, sender, engine = with_tasks(tmp_path, box, "2026-09-29T13:00:00Z")
    run(engine.tick())
    box[PK_A] = [task("WAITING_INPUT", since="2026-09-29T14:00:00Z")]
    clock.t = P.parse_utc("2026-09-29T18:00:00Z")
    run(engine.tick())
    assert len(sender.sent) == 2


def test_unknown_outcome_asks_for_confirmation(tmp_path):
    tasks = {PK_A: [task("UNKNOWN_OUTCOME", updated="2026-09-29T09:00:00Z")]}
    store, clock, sender, engine = with_tasks(tmp_path, tasks, "2026-09-29T10:00:00Z")
    run(engine.tick())
    assert len(sender.sent) == 1 and "подтверд" in sender.sent[0][1].lower()


def test_waiting_approval_followup(tmp_path):
    tasks = {PK_A: [task("WAITING_APPROVAL")]}
    store, clock, sender, engine = with_tasks(tmp_path, tasks, "2026-09-29T14:00:00Z")
    run(engine.tick())
    assert len(sender.sent) == 1 and "одобрен" in sender.sent[0][1].lower()


def test_done_tasks_are_silent_unless_the_participant_asked_for_finish_notices(tmp_path):
    tasks = {PK_A: [task("DONE", updated="2026-09-29T09:00:00Z")]}
    store, clock, sender, engine = with_tasks(tmp_path, tasks, "2026-09-29T10:00:00Z")
    run(engine.tick())
    assert sender.sent == []
    store.set_prefs(PK_A, notify_finished=True)
    run(engine.tick())
    assert len(sender.sent) == 1 and "готов" in sender.sent[0][1].lower()


def test_no_task_followups_without_consent(tmp_path):
    tasks = {PK_A: [task("WAITING_INPUT")]}
    store, clock, sender, engine = make(tmp_path, tasks=lambda pk: tasks.get(pk, []),
                                        clock=Clock(P.parse_utc("2026-09-29T14:00:00Z")))
    store.add_item(PK_A, "reminder", "seed", clock.t + 10 ** 6, "seed")     # participant is known
    run(engine.tick())
    assert sender.sent == []


def test_task_goal_in_followup_is_bounded_and_secret_free(tmp_path):
    goal = "Сделать " + "очень " * 60 + "s" + "k-" + "a1B2c3D4" * 5
    tasks = {PK_A: [task("WAITING_INPUT", goal=goal)]}
    store, clock, sender, engine = with_tasks(tmp_path, tasks, "2026-09-29T14:00:00Z")
    run(engine.tick())
    text = sender.sent[0][1]
    assert len(text) < 400 and "a1B2c3D4a1B2" not in text


def test_followups_read_the_real_task_store(tmp_path):
    from bcc.pit.tasks import TaskStore
    ts = TaskStore(tmp_path / "pit")
    created = ts.create(PK_A, "Подготовить план", [{"id": "s1", "title": "спросить", "needs_input": True}])
    asyncio.run(ts.run(PK_A, created["id"], {}))
    row = ts.get(PK_A, created["id"])
    assert row["state"] == "WAITING_INPUT"
    store = P.ProactiveStore(tmp_path / "personalities")
    clock = Clock(P.parse_utc(row["awaited_input"]["since"]) + 4 * 3600)
    sender = FakeSender()
    engine = P.ProactiveEngine(store, clock=clock, sender=sender, tasks=P.task_store_provider(ts),
                               default_tz_min=0)
    store.set_prefs(PK_A, proactive=True, min_gap_s=0, quiet_start=0, quiet_end=0)
    engine.watch(PK_A)
    run(engine.tick())
    assert len(sender.sent) == 1 and "Подготовить план" in sender.sent[0][1]


# -- daily digest -------------------------------------------------------------------------------------------
def test_digest_is_sent_once_a_day_at_the_digest_hour(tmp_path):
    tasks = {PK_A: [task("WAITING_INPUT")]}
    clock = Clock(local(2026, 9, 29, 8, 30))
    store, clock, sender, engine = make(tmp_path, tasks=lambda pk: tasks.get(pk, []), clock=clock)
    store.set_prefs(PK_A, proactive=True, digest=True, digest_hour=9, min_gap_s=0, max_per_day=10)
    run(engine.tick())
    assert sender.sent == []
    clock.t = local(2026, 9, 29, 9, 5)
    run(engine.tick())
    run(engine.tick())
    digests = [t for _, t, _ in sender.sent if "Сводка" in t]
    assert len(digests) == 1 and "Собрать отчёт" in digests[0]
    clock.t = local(2026, 9, 30, 9, 5)
    run(engine.tick())
    assert len([1 for _, t, _ in sender.sent if "Сводка" in t]) == 2


def test_digest_is_skipped_when_there_is_nothing_to_say(tmp_path):
    store, clock, sender, engine = make(tmp_path, clock=Clock(local(2026, 9, 29, 10, 0)))
    store.set_prefs(PK_A, proactive=True, digest=True, digest_hour=9)
    run(engine.tick())
    assert sender.sent == []


def test_digest_lists_todays_reminders(tmp_path):
    store, clock, sender, engine = make(tmp_path, clock=Clock(local(2026, 9, 29, 9, 10)))
    store.set_prefs(PK_A, proactive=True, digest=True, digest_hour=9, min_gap_s=0)
    store.add_item(PK_A, "reminder", "позвонить врачу", local(2026, 9, 29, 15, 0), "r1")
    run(engine.tick())
    digest = [t for _, t, _ in sender.sent if "Сводка" in t]
    assert digest and "позвонить врачу" in digest[0] and "15:00" in digest[0]


def test_digest_not_duplicated_after_restart(tmp_path):
    store, clock, sender, engine = make(tmp_path, clock=Clock(local(2026, 9, 29, 9, 10)))
    store.set_prefs(PK_A, proactive=True, digest=True, digest_hour=9, min_gap_s=0)
    store.add_item(PK_A, "reminder", "позвонить врачу", local(2026, 9, 29, 15, 0), "r1")
    run(engine.tick())
    _, _, _, engine2 = make(tmp_path, sender=sender, clock=clock)
    run(engine2.tick())
    assert len([1 for _, t, _ in sender.sent if "Сводка" in t]) == 1


def test_digest_off_by_default(tmp_path):
    store, clock, sender, engine = make(tmp_path, clock=Clock(local(2026, 9, 29, 10, 0)))
    store.set_prefs(PK_A, proactive=True)
    store.add_item(PK_A, "reminder", "x", local(2026, 9, 29, 15, 0), "r1")
    run(engine.tick())
    assert all("Сводка" not in t for _, t, _ in sender.sent)


# -- open threads --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,topic", [
    ("завтра у меня собеседование", "собеседование"),
    ("завтра экзамен, волнуюсь", "экзамен"),
    ("в пятницу у меня переговоры", "переговоры"),
])
def test_open_thread_detection(text, topic):
    found = P.detect_thread(text, NOW, TZ)
    assert found is not None and found.topic == topic
    assert found.due > NOW


def test_open_thread_ignores_ordinary_chat():
    assert P.detect_thread("как приготовить борщ", NOW, TZ) is None
    assert P.detect_thread("завтра будет дождь?", NOW, TZ) is None


# -- module hooks ---------------------------------------------------------------------------------------------
def ctx(text, mid="1", pk=PK_A, **kw):
    return TurnContext(person_key=pk, who="tg:1", text=text, message_id=mid, **kw)


def module(tmp_path, **kw):
    store, clock, sender, engine = make(tmp_path, **kw)
    return P.ProactiveModule(engine), store, clock, sender


def test_module_metadata():
    assert P.ProactiveModule.name == "proactive" and P.ProactiveModule.order == 80


def test_reminder_command_schedules_and_confirms(tmp_path):
    mod, store, clock, sender = module(tmp_path)
    advice = run(mod.pre_route(ctx("напомни завтра в 10 позвонить маме")))
    assert isinstance(advice, Advice) and "завтра в 10:00" in advice.reply and "позвонить маме" in advice.reply
    item = store.items(PK_A)[0]
    assert item["kind"] == "reminder" and fmt(item["due"]) == "2026-09-30 10:00"


def test_same_message_retried_creates_one_reminder(tmp_path):
    mod, store, *_ = module(tmp_path)
    run(mod.pre_route(ctx("напомни завтра в 10 позвонить маме", mid="77")))
    run(mod.pre_route(ctx("напомни завтра в 10 позвонить маме", mid="77")))
    assert len(store.items(PK_A)) == 1


def test_same_text_in_a_new_message_is_a_new_reminder(tmp_path):
    mod, store, *_ = module(tmp_path)
    run(mod.pre_route(ctx("напомни завтра в 10 позвонить маме", mid="77")))
    run(mod.pre_route(ctx("напомни завтра в 10 позвонить маме", mid="78")))
    assert len(store.items(PK_A)) == 2


def test_unparsable_reminder_asks_for_a_time_and_stores_nothing(tmp_path):
    mod, store, *_ = module(tmp_path)
    advice = run(mod.pre_route(ctx("напомни мне купить молоко")))
    assert advice and "когда" in advice.reply.lower()
    assert store.items(PK_A) == []


def test_ordinary_chat_is_untouched(tmp_path):
    mod, store, *_ = module(tmp_path)
    assert run(mod.pre_route(ctx("расскажи про Древний Рим"))) is None
    assert run(mod.pre_route(ctx("я напомнил ему вчера"))) is None
    assert store.items(PK_A) == []


def test_stop_command_opts_out_in_one_message(tmp_path):
    mod, store, *_ = module(tmp_path)
    run(mod.pre_route(ctx("напомни завтра в 10 позвонить")))
    advice = run(mod.pre_route(ctx("стоп напоминания")))
    assert "выключ" in advice.reply.lower()
    assert store.prefs(PK_A)["opted_out"] is True
    assert {i["state"] for i in store.items(PK_A)} == {"cancelled"}


def test_reminder_request_while_opted_out_explains_how_to_resume(tmp_path):
    mod, store, *_ = module(tmp_path)
    run(mod.pre_route(ctx("стоп напоминания")))
    advice = run(mod.pre_route(ctx("напомни завтра в 10")))
    assert "включи напоминания" in advice.reply
    assert store.items(PK_A) == []


def test_enable_command_gives_consent(tmp_path):
    mod, store, *_ = module(tmp_path)
    run(mod.pre_route(ctx("стоп напоминания")))
    advice = run(mod.pre_route(ctx("включи напоминания")))
    assert advice and store.prefs(PK_A)["proactive"] is True and store.prefs(PK_A)["opted_out"] is False


def test_list_and_cancel_commands(tmp_path):
    mod, store, *_ = module(tmp_path)
    run(mod.pre_route(ctx("напомни завтра в 10 позвонить маме", mid="1")))
    run(mod.pre_route(ctx("напомни завтра в 11 купить хлеб", mid="2")))
    listing = run(mod.pre_route(ctx("мои напоминания"))).reply
    assert "1." in listing and "2." in listing and "позвонить маме" in listing
    run(mod.pre_route(ctx("отмени напоминание 1")))
    assert [i["state"] for i in store.items(PK_A)].count("cancelled") == 1
    assert "купить хлеб" in run(mod.pre_route(ctx("мои напоминания"))).reply
    run(mod.pre_route(ctx("отмени все напоминания")))
    assert store.items(PK_A, states=("pending",)) == []


def test_listing_never_shows_another_participants_reminders(tmp_path):
    mod, store, *_ = module(tmp_path)
    run(mod.pre_route(ctx("напомни завтра в 10 тайное дело", pk=PK_B)))
    assert "тайное" not in run(mod.pre_route(ctx("мои напоминания", pk=PK_A))).reply


def test_timezone_and_quiet_hours_commands(tmp_path):
    mod, store, *_ = module(tmp_path)
    run(mod.pre_route(ctx("мой часовой пояс UTC+5")))
    assert store.prefs(PK_A)["tz_offset_min"] == 300
    run(mod.pre_route(ctx("тихие часы с 23 до 7")))
    prefs = store.prefs(PK_A)
    assert (prefs["quiet_start"], prefs["quiet_end"]) == (23, 7)
    advice = run(mod.pre_route(ctx("напомни завтра в 10")))
    assert "10:00" in advice.reply
    assert fmt(store.items(PK_A)[0]["due"], tz=300) == "2026-09-30 10:00"


def test_thread_is_recorded_only_with_consent(tmp_path):
    mod, store, *_ = module(tmp_path)
    assert run(mod.pre_route(ctx("завтра у меня собеседование"))) is None
    assert store.items(PK_A) == []
    store.set_prefs(PK_A, proactive=True)
    assert run(mod.pre_route(ctx("завтра у меня собеседование", mid="5"))) is None
    item = store.items(PK_A)[0]
    assert item["kind"] == "followup" and "собеседование" in item["text"]


def test_missed_reminder_is_appended_to_the_next_reply_once(tmp_path):
    mod, store, clock, sender = module(tmp_path, sender=FakeSender(["undeliverable"]))
    store.add_item(PK_A, "reminder", "купить хлеб", NOW, "r1")
    run(mod.engine.tick())
    first = run(mod.post_reply(ctx("привет"), "Привет!"))
    assert "купить хлеб" in first and first.startswith("Привет!")
    assert run(mod.post_reply(ctx("ещё"), "Ответ")) is None


def test_status_reports_counts_without_text(tmp_path):
    mod, store, *_ = module(tmp_path)
    run(mod.pre_route(ctx("напомни завтра в 10 тайное дело")))
    status = mod.status()
    assert status["name"] == "proactive" and status["pending"] == 1 and status["participants"] == 1
    assert "тайное" not in str(status)


def test_background_loop_ticks_and_stops(tmp_path):
    async def scenario():
        store, clock, sender, engine = make(tmp_path)
        store.add_item(PK_A, "reminder", "x", NOW, "r1")
        ticked = asyncio.Event()
        original = engine.tick

        async def counting():
            result = await original()
            ticked.set()
            return result
        engine.tick = counting

        async def fake_sleep(_):
            await asyncio.sleep(3600)

        mod = P.ProactiveModule(engine, sleep=fake_sleep)
        await mod.start()
        await asyncio.wait_for(ticked.wait(), 2)
        assert mod.status()["running"] is True
        await mod.stop()
        assert mod.status()["running"] is False
        return sender.sent

    assert len(run(scenario())) == 1


def test_loop_survives_a_failing_tick(tmp_path):
    async def scenario():
        store, clock, sender, engine = make(tmp_path)
        calls = []
        done = asyncio.Event()

        async def boom():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("tick fault")
            done.set()
            return {}
        engine.tick = boom

        async def quick_sleep(_):
            await asyncio.sleep(0)

        mod = P.ProactiveModule(engine, sleep=quick_sleep)
        await mod.start()
        await asyncio.wait_for(done.wait(), 2)
        await mod.stop()
        return len(calls)

    assert run(scenario()) >= 3


def test_works_inside_the_pipeline(tmp_path):
    mod, store, *_ = module(tmp_path)
    pipeline = J2Pipeline([mod])
    reply = run(pipeline.pre_route(ctx("напомни завтра в 10 позвонить маме")))
    assert reply and "позвонить маме" in reply


def test_create_builds_from_a_runtime_like_object(tmp_path):
    class Vault:
        root = tmp_path / "personalities"
        data_dir = tmp_path

    class Runtime:
        vault = Vault()
        home = tmp_path / "pit-v1.7"

    Vault.root.mkdir()
    mod = P.create(Runtime())
    assert mod.name == "proactive"
    run(mod.pre_route(ctx("напомни завтра в 10 позвонить маме")))
    assert (Vault.root / PK_A / "proactive" / "schedule.json").is_file()


def test_telegram_sender_maps_person_key_and_reports_undeliverable():
    from bcc.pit.identity import derive_person_key
    from bcc.telegram_companion.config import Person
    salt = b"s" * 32
    sent = []

    class Tg:
        async def send(self, person, text, **kw):
            sent.append((person.user_id, text))
            return 5

    class Runtime:
        telegram = Tg()

        class settings:
            people = (Person(1001, 1001, "owner"),)

    key = derive_person_key(1001, salt)
    sender = P.TelegramSender(Runtime(), salt=salt)
    assert run(sender(key, "привет", "k")) == "sent" and sent == [(1001, "привет")]
    assert run(sender(PK_A, "привет", "k")) == "undeliverable"
