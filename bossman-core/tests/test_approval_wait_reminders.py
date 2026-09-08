"""Ожидание подтверждения не должно быть немым.

`approvals.wait()` держал задачу в waiting_approval до суток, а спрашивал
владельца ровно один раз — в `create()`. Потерянное сообщение (диспетчер лежал,
чат заглушен, сообщение пролистано) означало, что задача досидит весь таймаут,
и никто больше не будет спрошен: это тот же отказ, что и review-deadlock в
Command Center, только с другой стороны. Плюс плоский опрос раз в 2 c —
43 200 запросов на одно суточное ожидание.

Здесь проверяется, что ожидание стало слышимым и дешёвым, и — отдельно и
намеренно — что права оно при этом не расширило: повторный вопрос остаётся
вопросом, решение по-прежнему принимает владелец, а сломанный канал
уведомлений не превращает неотвеченный запрос в выполненное действие.
"""
from __future__ import annotations

import asyncio

import pytest

from bossman import approvals as approvals_mod

pytestmark = pytest.mark.asyncio


class _Fake:
    """Строка approvals + перехват событий и уведомлений."""

    def __init__(self, status="pending", row=True, preview="rm -rf /var/tmp/x",
                 kind="tool", tool="shell"):
        self.row = ({"id": 7, "status": status, "preview": preview,
                     "kind": kind, "tool": tool} if row else None)
        self.selects = 0
        self.executed: list[str] = []
        self.asked: list[tuple[int, str]] = []
        self.events: list[tuple[str, dict]] = []
        self.sleeps: list[float] = []
        self.ask_error: Exception | None = None

    async def fetchrow(self, sql, *args):
        if "SELECT" in sql:
            self.selects += 1
        return dict(self.row) if self.row else None

    async def execute(self, sql, *args):
        self.executed.append(sql)
        if "expired" in sql and self.row and self.row["status"] == "pending":
            self.row["status"] = "expired"
        return None

    async def ask_approval(self, approval_id, preview):
        if self.ask_error:
            raise self.ask_error
        self.asked.append((approval_id, preview))

    def emit(self, kind, **data):
        self.events.append((kind, data))

    async def sleep(self, seconds):
        self.sleeps.append(seconds)

    def kinds(self, name):
        return [d for k, d in self.events if k == name]


@pytest.fixture
def fake(monkeypatch):
    f = _Fake()
    monkeypatch.setattr(approvals_mod.db, "fetchrow", f.fetchrow)
    monkeypatch.setattr(approvals_mod.db, "execute", f.execute)
    monkeypatch.setattr(approvals_mod.telegram, "ask_approval", f.ask_approval)
    monkeypatch.setattr(approvals_mod.events, "emit", f.emit)
    monkeypatch.setattr(approvals_mod.asyncio, "sleep", f.sleep)
    return f


# ------------------------------------------------- молчание больше не вариант

async def test_an_unanswered_request_is_put_back_in_front_of_the_owner(fake):
    """Один вопрос при создании — это одна попытка. Ожидание в час обязано
    спросить ещё раз, иначе потерянное сообщение стоит задаче всего таймаута."""
    out = await approvals_mod.wait(7, timeout_s=3600)
    assert out["status"] == "expired"
    assert len(fake.asked) >= 3, "владельца не переспросили ни разу"
    assert all(aid == 7 for aid, _ in fake.asked)


async def test_the_reminder_carries_the_same_request_not_a_new_one(fake):
    """Повторный вопрос не создаёт нового подтверждения и не меняет предмет:
    иначе «напоминание» было бы вторым каналом выдачи прав."""
    await approvals_mod.wait(7, timeout_s=3600)
    assert {preview for _, preview in fake.asked} == {"rm -rf /var/tmp/x"}
    assert not any("INSERT" in sql for sql in fake.executed)


async def test_waiting_is_visible_in_the_event_stream(fake):
    """Раньше между approval.created и (может быть) approval.decided не было
    ничего: снаружи «висит» и «забыто» выглядели одинаково."""
    await approvals_mod.wait(7, timeout_s=3600)
    waiting = fake.kinds("approval.waiting")
    assert waiting, "не видно, что запрос ждёт"
    assert [w["waited_s"] for w in waiting] == sorted(w["waited_s"] for w in waiting)
    assert waiting[0]["waited_s"] >= approvals_mod.REMIND_AFTER_SECONDS[0]
    assert waiting[0]["approval_kind"] == "tool" and waiting[0]["tool"] == "shell"


async def test_the_waiting_event_does_not_carry_the_preview(fake):
    """Поток событий шире, чем канал подтверждений; текст команды туда не идёт."""
    await approvals_mod.wait(7, timeout_s=3600)
    for payload in fake.kinds("approval.waiting"):
        assert "preview" not in payload
        assert "rm -rf" not in str(payload)


async def test_a_short_wait_does_not_pester_anyone(fake):
    """Первая отметка — минута. Двухсекундное ожидание не спрашивает заново."""
    await approvals_mod.wait(7, timeout_s=2)
    assert fake.asked == []
    assert fake.kinds("approval.waiting") == []


# ----------------------------------------------- сломанный канал ≠ разрешение

async def test_a_broken_notification_channel_does_not_release_the_wait(fake):
    """Если бы падение telegram снимало ожидание, неотвеченный запрос стал бы
    выполненным действием — ровно то, от чего этот гейт существует."""
    fake.ask_error = RuntimeError("dispatcher down")
    out = await approvals_mod.wait(7, timeout_s=3600)
    assert out["status"] == "expired"
    assert fake.kinds("approval.remind_failed"), "провал напоминания скрыт"
    assert "RuntimeError" in fake.kinds("approval.remind_failed")[0]["error"]


async def test_a_broken_channel_never_reports_approved(fake):
    fake.ask_error = RuntimeError("boom")
    out = await approvals_mod.wait(7, timeout_s=3600)
    assert out["status"] != "approved"


# --------------------------------------------------------- цена ожидания

async def test_a_long_wait_stops_hammering_the_database(fake):
    """Плоские 2 c — это 1800 запросов в час на одно подтверждение. Опрос
    разгоняется до потолка, поэтому час стоит сотни, а не тысячи."""
    await approvals_mod.wait(7, timeout_s=3600)
    assert fake.selects < 400, f"{fake.selects} запросов за час ожидания"
    assert fake.sleeps[-1] <= approvals_mod.POLL_MAX_SECONDS


async def test_the_first_seconds_are_still_polled_tightly(fake):
    """Обычный случай — владелец решает сразу; разгон не должен добавлять ему
    секунд ожидания на пустом месте."""
    await approvals_mod.wait(7, timeout_s=3600)
    assert fake.sleeps[0] <= 1.0


async def test_a_decision_taken_while_waiting_wins(fake):
    """Регрессия: разгон опроса не должен «проспать» решение."""
    async def flip(sql, *args):
        row = await _Fake.fetchrow(fake, sql, *args)
        if "SELECT" in sql and fake.selects == 2:
            fake.row["status"] = "approved"
            fake.row["decided_by"] = "owner"
        return row

    fake_fetch = flip
    approvals_mod.db.fetchrow = fake_fetch
    out = await approvals_mod.wait(7, timeout_s=3600)
    assert out["status"] == "approved" and out["decided_by"] == "owner"
    assert not any("expired" in sql for sql in fake.executed)


# ---------------------------------------------------------- честные ответы

async def test_a_missing_row_is_answered_at_once(fake):
    """Строки нет — решать нечего и некому. Ждать сутки ради того же ответа
    значит держать задачу в waiting_approval из-за опечатки в id."""
    fake.row = None
    out = await approvals_mod.wait(7, timeout_s=3600)
    assert out == {"id": 7, "status": "expired"}
    assert fake.selects == 1
    assert fake.kinds("approval.missing")


async def test_an_already_decided_request_returns_without_waiting(fake):
    fake.row["status"] = "rejected"
    out = await approvals_mod.wait(7, timeout_s=3600)
    assert out["status"] == "rejected"
    assert fake.sleeps == [] and fake.asked == []


async def test_the_wait_is_bounded_by_the_timeout_it_was_given(fake):
    """Прежний `range(timeout_s // 2)` не считал время запроса к БД, поэтому
    номинальный таймаут и настоящий расходились."""
    await approvals_mod.wait(7, timeout_s=120)
    assert sum(fake.sleeps) <= 120 + 1e-6


# ------------------------------------------------------- окно решения владельца

async def test_the_default_window_is_still_a_full_day(monkeypatch):
    """Сузить окно молча значило бы отнять у владельца право решить позже."""
    monkeypatch.delenv(approvals_mod.TIMEOUT_ENV, raising=False)
    assert approvals_mod._default_timeout() == 24 * 3600


async def test_the_window_is_configurable(monkeypatch):
    monkeypatch.setenv(approvals_mod.TIMEOUT_ENV, "1800")
    assert approvals_mod._default_timeout() == 1800


async def test_garbage_in_the_env_falls_back_to_the_day(monkeypatch):
    """Мусор в переменной не должен ни ронять ожидание, ни делать его вечным."""
    for value in ("", "по-больше", "-5", "3.5"):
        monkeypatch.setenv(approvals_mod.TIMEOUT_ENV, value)
        assert approvals_mod._default_timeout() == 24 * 3600, value


async def test_the_default_is_used_when_no_timeout_is_passed(fake, monkeypatch):
    monkeypatch.setenv(approvals_mod.TIMEOUT_ENV, "0")
    out = await approvals_mod.wait(7)
    assert out["status"] == "expired"
    assert fake.sleeps == []


# ------------------------------------------------------------- скоупы событий

async def test_the_new_event_kinds_stay_behind_the_approve_scope():
    """approval.waiting несёт id и вид действия. Утечь в chat-скоуп он не должен
    — как и approval.created."""
    from bossman.remote_client.auth import SCOPE_APPROVE
    from bossman.remote_client.events import event_allowed, event_required_scope

    for kind in ("approval.waiting", "approval.remind_failed", "approval.missing"):
        assert event_required_scope(kind) == SCOPE_APPROVE
        assert event_allowed(kind, {"chat", "events"}) is False
