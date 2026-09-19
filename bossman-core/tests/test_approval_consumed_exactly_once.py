"""Одобрение потребляется РОВНО ОДИН РАЗ — иначе задача возобновится дважды.

Владелец назвал это свойство прямо: «authenticated approval consumed → task
resumes exactly once». В коде оно держится на одной оговорке в SQL:

    UPDATE approvals SET status=$2 ... WHERE id=$1 AND status='pending'

Убери `AND status='pending'` — и повторная отправка того же решения снова
вернёт строку и снова выпустит событие `approval.decided`. Задача, ждущая
одобрения, возобновится второй раз.

Обход тестов показал, что ни один из них этого не ловит: все девять файлов про
одобрения работают через `fake_decide`/`spy_decide`, то есть подменяют саму
`decide()` и её SQL не исполняют вовсе. Удаление оговорки оставило бы набор
зелёным.

ЧТО ЭТИ ТЕСТЫ ДОКАЗЫВАЮТ И ЧЕГО НЕ ДОКАЗЫВАЮТ — честно:

* доказывают поведение `decide()` НА СТОРОНЕ ВЫЗЫВАЮЩЕГО: при уже решённом
  одобрении она возвращает None и НЕ выпускает события;
* доказывают структурно, что оговорка на месте в тексте запроса;
* НЕ доказывают семантику самого Postgres. Настоящее исполнение UPDATE живёт
  в полосе postgres-contracts, и здесь это не подменяется. Замена лишь
  ИСПОЛНЯЕТ смысл оговорки: убрали оговорку — замена перестаёт защищать, и
  поведенческие проверки краснеют вместе с текстовой.
"""
from __future__ import annotations

import asyncio

import pytest

from bossman import approvals as approvals_mod
from bossman import events


class _Store:
    """Крошечная замена таблице: хранит статус и уважает оговорку pending.

    Замена НЕ разбирает SQL. Она воспроизводит ровно то поведение, которое
    оговорка задаёт в Postgres: обновить и вернуть строку только если запись
    ещё `pending`. Если оговорку убрать из кода, тест структурный ниже
    покраснеет, а этот останется честным описанием ожидаемого поведения.
    """

    def __init__(self, status: str = "pending") -> None:
        self.status = status
        self.statements: list[str] = []

    async def fetchrow(self, sql, *args):
        flat = " ".join(sql.split())
        self.statements.append(flat)
        approval_id, new_status, decided_by = args
        # Замена ИСПОЛНЯЕТ смысл оговорки, а не предполагает её. Если запрос
        # ограничен `status='pending'` — обновляем только ожидающую запись,
        # как это сделал бы Postgres; если оговорку убрали — обновляем всегда,
        # тоже как Postgres. Поэтому удаление оговорки делает красными и
        # поведенческие проверки, а не только текстовую.
        guarded = "status='pending'" in flat.replace("status = 'pending'", "status='pending'")
        if guarded and self.status != "pending":
            return None
        self.status = new_status
        return {"id": approval_id, "status": new_status, "decided_by": decided_by}


@pytest.fixture
def store(monkeypatch):
    made = _Store()
    monkeypatch.setattr(approvals_mod.db, "fetchrow", made.fetchrow, raising=False)
    return made


def _emitted(monkeypatch) -> list[tuple]:
    seen: list[tuple] = []
    monkeypatch.setattr(events, "emit", lambda name, **kw: seen.append((name, kw)))
    monkeypatch.setattr(approvals_mod.events, "emit",
                        lambda name, **kw: seen.append((name, kw)), raising=False)
    return seen


def test_the_first_decision_is_accepted(store, monkeypatch):
    """Положительная половина. Без неё «второй раз не проходит» выполнялось бы
    и сломанной функцией, которая не работает никогда."""
    seen = _emitted(monkeypatch)
    row = asyncio.run(approvals_mod.decide(7, True, "owner"))
    assert row is not None and row["status"] == "approved"
    assert [name for name, _ in seen] == ["approval.decided"]


def test_a_second_decision_on_the_same_approval_is_refused(store, monkeypatch):
    """Отрицательный контроль: повтор не возвращает строку и НЕ шлёт события.

    Событие здесь важнее возвращаемого значения: на `approval.decided`
    подписана та часть, что возобновляет задачу. Лишнее событие — это лишний
    запуск.
    """
    asyncio.run(approvals_mod.decide(7, True, "owner"))
    seen = _emitted(monkeypatch)
    again = asyncio.run(approvals_mod.decide(7, True, "owner"))
    assert again is None, "повторное решение вернуло строку — задача возобновится дважды"
    assert seen == [], f"повторное решение выпустило события: {seen}"


def test_a_rejection_cannot_be_flipped_to_approval_afterwards(store, monkeypatch):
    """Отказ — тоже решение. Переголосовать его вторым вызовом нельзя."""
    asyncio.run(approvals_mod.decide(9, False, "owner"))
    seen = _emitted(monkeypatch)
    flipped = asyncio.run(approvals_mod.decide(9, True, "owner"))
    assert flipped is None, "отказ удалось перевести в одобрение повторным вызовом"
    assert seen == []
    assert store.status == "rejected"


def test_the_statement_still_carries_the_pending_guard(store):
    """Структурный контракт на саму оговорку.

    Текстовая проверка названа тем, что она есть: она ловит УДАЛЕНИЕ оговорки,
    а не доказывает семантику Postgres. Вместе с поведенческой парой выше этого
    достаточно, чтобы потеря одноразовости не прошла незамеченной.
    """
    asyncio.run(approvals_mod.decide(11, True, "owner"))
    assert store.statements, "decide() не обратилась к базе вовсе"
    sql = store.statements[0]
    assert "status='pending'" in sql.replace("status = 'pending'", "status='pending'"), (
        f"в запросе нет оговорки about pending — одобрение станет повторяемым: {sql}"
    )
