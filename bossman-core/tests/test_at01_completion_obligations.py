"""AT-01 на ТЕКУЩЕМ кандидате: COMPLETE — это исход, а не слово.

Пять враждебных случаев из требования владельца. Каждый из них — правдоподобный
способ закрыть задачу, ничего не сделав, и каждый обязан получить отказ:

1. запрошенного файла нет;
2. файл есть, но содержимое не то, что просили;
3. файл лежал там ДО попытки и не менялся (доказывает прошлое, а не работу);
4. обязательств несколько, выполнена часть;
5. произошла посторонняя подтверждённая мутация, и следом COMPLETE.

Пятый — тот, ради которого пишется весь слой. Прежнее правило («была хотя бы
одна подтверждённая изменяющая операция») закрывало ЛЮБУЮ цель ЛЮБОЙ мутацией:
открыть блокнот и что-то напечатать засчитывалось за «создай отчёт report.txt».

Плюс два положительных контроля — без них «всё красное» тоже выглядит как PASS.

Проверка исхода читает НАСТОЯЩИЙ диск: tmp_path, реальные файлы, независимо от
модели и от экрана.
"""
from __future__ import annotations

import asyncio

import pytest

from bossman.computer_operator.models import (ActionKind, ComputerAction, ExpectedState, TaskState)
from bossman.computer_operator.obligations import file_probe
from bossman.computer_operator.wiring import FakeAdapter, FakeObserver, FakePlanner, make_manager


def complete():
    # Постусловие непустое и совпадает с экраном: планировщик «честно» доложил
    # об успехе. Отказ обязан прийти от МИРА, а не от постусловия.
    return ComputerAction.make(ActionKind.COMPLETE, expected=ExpectedState(contains_text="ok"))


def typed(text="что-то"):
    return ComputerAction.make(ActionKind.TYPE, text=text,
                               expected=ExpectedState(contains_text="ok"))


def operator(tmp_path, goal, actions, *, root=None):
    return make_manager(tmp_path / "tasks.json", FakePlanner(list(actions)),
                        FakeObserver(summary="ok"), adapter=FakeAdapter(),
                        obligation_probe=file_probe(root or tmp_path)), goal


async def run(mgr, goal):
    t = mgr.create_task(goal)
    state = await asyncio.wait_for(mgr.run(t.id), timeout=20)
    return state, mgr.store.get(t.id)


# ------------------------------------------------------------------ негативы

@pytest.mark.asyncio
async def test_1_a_missing_requested_file_refuses_completion(tmp_path):
    mgr, goal = operator(tmp_path, 'создай файл report.txt с текстом "Готово"',
                         [typed(), complete()] + [complete()] * 30)
    state, row = await run(mgr, goal)
    assert state is not TaskState.COMPLETED
    assert "не создан" in (row.last_error or ""), row.last_error
    assert mgr.completions_refused >= 1


@pytest.mark.asyncio
async def test_2_wrong_contents_refuse_completion(tmp_path):
    (tmp_path / "report.txt").write_text("совсем другое", encoding="utf-8")
    mgr, goal = operator(tmp_path, 'создай файл report.txt с текстом "Готово"',
                         [typed(), complete()] + [complete()] * 30)
    state, row = await run(mgr, goal)
    assert state is not TaskState.COMPLETED
    assert "содержимое не соответствует" in (row.last_error or ""), row.last_error


@pytest.mark.asyncio
async def test_3_a_stale_pre_existing_file_is_not_evidence(tmp_path):
    """Файл с ПРАВИЛЬНЫМ содержимым, но существовавший до начала попытки."""
    (tmp_path / "report.txt").write_text("Готово", encoding="utf-8")
    mgr, goal = operator(tmp_path, 'создай файл report.txt с текстом "Готово"',
                         [typed(), complete()] + [complete()] * 30)
    state, row = await run(mgr, goal)
    assert state is not TaskState.COMPLETED
    assert "существовал до начала" in (row.last_error or ""), row.last_error


@pytest.mark.asyncio
async def test_4_partial_completion_of_several_obligations_refuses(tmp_path):
    goal = "напиши отчёт report.txt и сводку summary.md"

    class WritesOne(FakeAdapter):
        async def execute(self, a, o):
            if a.kind is ActionKind.TYPE:
                (tmp_path / "report.txt").write_text("отчёт", encoding="utf-8")
            return await super().execute(a, o)

    mgr = make_manager(tmp_path / "tasks.json",
                       FakePlanner([typed(), complete()] + [complete()] * 30),
                       FakeObserver(summary="ok"), adapter=WritesOne(),
                       obligation_probe=file_probe(tmp_path))
    state, row = await run(mgr, goal)
    assert state is not TaskState.COMPLETED
    assert "summary.md" in (row.last_error or ""), row.last_error
    assert (tmp_path / "report.txt").is_file(), "первое обязательство должно быть выполнено"


@pytest.mark.asyncio
async def test_5_an_unrelated_verified_mutation_does_not_buy_completion(tmp_path):
    """Тот самый случай, который прежнее правило пропускало.

    Подтверждённая изменяющая операция есть — и она посторонняя: записан другой
    файл. Цель просила report.txt. «Какая-то мутация была» не улика.
    """
    class WritesSomethingElse(FakeAdapter):
        async def execute(self, a, o):
            if a.kind is ActionKind.TYPE:
                (tmp_path / "notes-of-the-model.txt").write_text("я поработал", encoding="utf-8")
            return await super().execute(a, o)

    mgr = make_manager(tmp_path / "tasks.json",
                       FakePlanner([typed(), complete()] + [complete()] * 30),
                       FakeObserver(summary="ok"), adapter=WritesSomethingElse(),
                       obligation_probe=file_probe(tmp_path))
    state, row = await run(mgr, 'создай файл report.txt с текстом "Готово"')
    assert state is not TaskState.COMPLETED
    assert "report.txt" in (row.last_error or ""), row.last_error
    # Мутация действительно была и действительно подтверждена — то есть отказ
    # пришёл именно от обязательства, а не от отсутствия шагов.
    assert (tmp_path / "notes-of-the-model.txt").is_file()
    assert any(s.verified for s in mgr.store.get(mgr.store.list()[0].id).history)


# ------------------------------------------------------------- положительные

@pytest.mark.asyncio
async def test_positive_1_a_read_only_task_completes_normally(tmp_path):
    """Наблюдательная цель ничего снаружи не обещала — и закрывается."""
    mgr, goal = operator(tmp_path, "опиши, что сейчас на экране", [complete()])
    state, row = await run(mgr, goal)
    assert state is TaskState.COMPLETED, row.last_error
    assert mgr.completions_refused == 0


@pytest.mark.asyncio
async def test_positive_2_the_exact_requested_effect_completes(tmp_path):
    """Тот самый файл, с тем самым текстом, созданный В ЭТОЙ попытке."""
    class WritesTheRequestedFile(FakeAdapter):
        async def execute(self, a, o):
            if a.kind is ActionKind.TYPE:
                (tmp_path / "report.txt").write_text("Готово", encoding="utf-8")
            return await super().execute(a, o)

    mgr = make_manager(tmp_path / "tasks.json",
                       FakePlanner([typed(), complete()]),
                       FakeObserver(summary="ok"), adapter=WritesTheRequestedFile(),
                       obligation_probe=file_probe(tmp_path))
    state, row = await run(mgr, 'создай файл report.txt с текстом "Готово"')
    assert state is TaskState.COMPLETED, row.last_error
    assert (tmp_path / "report.txt").read_text(encoding="utf-8") == "Готово"


@pytest.mark.asyncio
async def test_positive_3_an_updated_pre_existing_file_counts(tmp_path):
    """Отрицательный контроль к случаю 3: файл БЫЛ, но эта попытка его изменила."""
    (tmp_path / "report.txt").write_text("старое", encoding="utf-8")

    class Updates(FakeAdapter):
        async def execute(self, a, o):
            if a.kind is ActionKind.TYPE:
                (tmp_path / "report.txt").write_text("Готово", encoding="utf-8")
            return await super().execute(a, o)

    mgr = make_manager(tmp_path / "tasks.json",
                       FakePlanner([typed(), complete()]),
                       FakeObserver(summary="ok"), adapter=Updates(),
                       obligation_probe=file_probe(tmp_path))
    state, row = await run(mgr, 'создай файл report.txt с текстом "Готово"')
    assert state is TaskState.COMPLETED, row.last_error
