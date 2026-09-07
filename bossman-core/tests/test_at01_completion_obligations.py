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

Хвост файла (P0-3) закрывает последнюю дыру этого слоя: UnknownEffect. Цель,
чей результат не удалось ни типизировать, ни прочитать, ни даже извлечь,
теперь fail-closed — НЕ ВЫПОЛНЕНА, чем бы ни закончились соседние шаги.

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


def typed(text="что-то", seeing="ok"):
    return ComputerAction.make(ActionKind.TYPE, text=text,
                               expected=ExpectedState(contains_text=seeing))


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


# ---------------- AT-01 за пределами файловых обязательств ----------------
#
# Слой понимал только пути. Цель «включи тёмную тему» не давала ни одного
# обязательства, и решение падало обратно на слабое правило: любая
# подтверждённая мутация закрывала любую цель.

def screen(text):
    return FakeObserver(summary=text)


def complete_seeing(text):
    """COMPLETE с постусловием, которое ДЕЙСТВИТЕЛЬНО есть на экране."""
    return ComputerAction.make(ActionKind.COMPLETE, expected=ExpectedState(contains_text=text))


class ChangesScreen(FakeAdapter):
    """Адаптер, который МЕНЯЕТ экран наблюдателя на заданный."""

    def __init__(self, observer, after, **kw):
        super().__init__(**kw)
        self.observer = observer
        self.after = after

    async def execute(self, a, o):
        if a.kind is ActionKind.TYPE:
            self.observer.summary = self.after
        return await super().execute(a, o)


@pytest.mark.asyncio
async def test_a_screen_goal_is_refused_when_the_screen_never_showed_it(tmp_path):
    """Негативный не-файловый случай: обещано «Dark», на экране его нет."""
    observer = screen("light theme ok")
    mgr = make_manager(tmp_path / "t.json",
                       FakePlanner([typed("x", seeing="light"), complete_seeing("light")] + [complete_seeing("light")] * 30),
                       observer, adapter=ChangesScreen(observer, "still light ok"))
    t = mgr.create_task('включи тёмную тему "Dark"')
    state = await asyncio.wait_for(mgr.run(t.id), timeout=20)
    assert state is not TaskState.COMPLETED
    assert "Dark" in (mgr.store.get(t.id).last_error or "")


@pytest.mark.asyncio
async def test_a_screen_goal_is_refused_when_it_was_already_there(tmp_path):
    """Улика попытки: обещанное было на экране ДО начала — это не результат."""
    observer = screen("Dark theme already ok")
    mgr = make_manager(tmp_path / "t.json",
                       FakePlanner([typed("x", seeing="Dark"), complete_seeing("Dark")] + [complete_seeing("Dark")] * 30),
                       observer, adapter=ChangesScreen(observer, "Dark theme already ok too"))
    t = mgr.create_task('включи тёмную тему "Dark"')
    state = await asyncio.wait_for(mgr.run(t.id), timeout=20)
    assert state is not TaskState.COMPLETED
    assert "до попытки" in (mgr.store.get(t.id).last_error or "")


@pytest.mark.asyncio
async def test_a_screen_goal_completes_when_the_attempt_put_it_there(tmp_path):
    """Положительный контроль: экран изменился и показывает обещанное."""
    observer = screen("light theme ok")
    mgr = make_manager(tmp_path / "t.json",
                       FakePlanner([typed("x", seeing="theme"), complete_seeing("Dark")]),
                       observer, adapter=ChangesScreen(observer, "Dark theme ok"))
    t = mgr.create_task('включи тёмную тему "Dark"')
    state = await asyncio.wait_for(mgr.run(t.id), timeout=20)
    assert state is TaskState.COMPLETED, mgr.store.get(t.id).last_error


@pytest.mark.asyncio
async def test_an_unextractable_effect_fails_closed_until_a_verifier_exists(tmp_path):
    """Эффект без проверяемого результата не может автоматически стать COMPLETE.

    Раньше здесь стоял «признанный предел»: обязательство молча снималось, и
    завершение решало прежнее слабое правило — любая подтверждённая мутация
    закрывала любую цель. Теперь fail-closed с обеих сторон: `extract_obligations`
    отдаёт отдельный маркер `UnverifiableEffect` (PR #37), а менеджер больше не
    выбрасывает ни его, ни исторический `UnknownEffect` (P0-3). Закрыть такое
    обязательство можно только привязанной квитанцией исполнителя — см.
    положительный контроль ниже, чтобы строгость не выродилась в «никогда не
    завершается».
    """
    from bossman.computer_operator.obligations import UnverifiableEffect, extract_obligations

    assert extract_obligations("оплати счёт") == (
        UnverifiableEffect(reason="из цели не извлечён проверяемый результат"),)

    observer = screen("invoice open")
    mgr = make_manager(tmp_path / "t.json",
                       FakePlanner([typed("x", seeing="invoice"), complete_seeing("invoice")] +
                                   [complete_seeing("invoice")] * 30),
                       observer, adapter=ChangesScreen(observer, "invoice paid"))
    t = mgr.create_task("оплати счёт")
    state = await asyncio.wait_for(mgr.run(t.id), timeout=20)
    row = mgr.store.get(t.id)
    assert state is not TaskState.COMPLETED
    assert mgr.completions_refused >= 1
    assert "не извлечён проверяемый результат" in (row.last_error or "")
    # Экран за попытку ИЗМЕНИЛСЯ («invoice paid») — и этого по-прежнему мало:
    # изменение экрана не привязано к обещанию, а квитанции нет.
    assert "квитанции" in (row.last_error or ""), row.last_error


# ==================================================================== P0-3
# AT-01, UnknownEffect: неизвестный, нетипизируемый или непроверяемый результат
# закрывает задачу как НЕ ВЫПОЛНЕННУЮ, что бы ни было подтверждено рядом.
# Улика закрывает ТОЛЬКО то обязательство, к которому она привязана.

from bossman.computer_operator.obligations import (  # noqa: E402  (рядом с тестами P0-3)
    EffectReceipt, FileEffect, UnknownEffect, UnverifiableEffect)


def _receipt_port(adapter, detail="POST /payments -> txn_42", task_id=None):
    """Порт квитанций исполнителя: выдаёт улику ТОЛЬКО после реального эффекта."""
    def receipts(task, obligations):
        if not adapter.executed:
            return ()
        return [EffectReceipt(key=o.key(), task_id=task_id or task.id, detail=detail)
                for o in obligations if isinstance(o, (UnknownEffect, UnverifiableEffect))]
    return receipts


@pytest.mark.asyncio
async def test_p0_3_unknown_effect_is_not_bought_by_an_unrelated_mutation(tmp_path):
    """1/6: неизвестный эффект + посторонняя подтверждённая мутация -> НЕ ВЫПОЛНЕНО."""
    class WritesSomethingElse(FakeAdapter):
        async def execute(self, a, o):
            if a.kind is ActionKind.TYPE:
                (tmp_path / "scratch.txt").write_text("я поработал", encoding="utf-8")
            return await super().execute(a, o)

    adapter = WritesSomethingElse()
    mgr = make_manager(tmp_path / "t.json",
                       FakePlanner([typed(), complete()] + [complete()] * 30),
                       FakeObserver(summary="ok"), adapter=adapter,
                       obligation_probe=file_probe(tmp_path))
    state, row = await run(mgr, "оплати счёт")
    assert state is not TaskState.COMPLETED
    assert "квитанции" in (row.last_error or ""), row.last_error
    # Мутация действительно была и действительно подтверждена: отказ пришёл от
    # обязательства, а не от отсутствия шагов.
    assert (tmp_path / "scratch.txt").is_file()
    assert any(s.verified for s in row.history)


@pytest.mark.asyncio
async def test_p0_3_zero_extracted_obligations_are_not_success(tmp_path):
    """2/6: пустой набор обязательств -> НЕ ВЫПОЛНЕНО, даже если эффект реален.

    Подменный классификатор, вернувший «обязательств нет», не имеет права
    открыть дверь: «проверять нечего» — это «нечем проверить», а не «успех».
    """
    class WritesTheRequestedFile(FakeAdapter):
        async def execute(self, a, o):
            if a.kind is ActionKind.TYPE:
                (tmp_path / "report.txt").write_text("Готово", encoding="utf-8")
            return await super().execute(a, o)

    mgr = make_manager(tmp_path / "t.json",
                       FakePlanner([typed(), complete()] + [complete()] * 30),
                       FakeObserver(summary="ok"), adapter=WritesTheRequestedFile(),
                       obligation_probe=file_probe(tmp_path),
                       obligations_of=lambda goal: ())
    state, row = await run(mgr, 'создай файл report.txt с текстом "Готово"')
    assert state is not TaskState.COMPLETED
    assert "пустой набор обязательств" in (row.last_error or ""), row.last_error
    assert (tmp_path / "report.txt").is_file(), "эффект был — отказ пришёл именно от пустого набора"


@pytest.mark.asyncio
async def test_p0_3_a_parser_failure_is_not_swallowed_into_success(tmp_path):
    """3/6: падение разбора цели -> НЕ ВЫПОЛНЕНО (и цикл не падает)."""
    def explodes(goal):
        raise RuntimeError("извлечение обязательств сломалось")

    mgr = make_manager(tmp_path / "t.json",
                       FakePlanner([typed(), complete()] + [complete()] * 30),
                       FakeObserver(summary="ok"), adapter=FakeAdapter(),
                       obligation_probe=file_probe(tmp_path), obligations_of=explodes)
    state, row = await run(mgr, 'создай файл report.txt с текстом "Готово"')
    assert state is not TaskState.COMPLETED
    assert "разбор цели упал: RuntimeError" in (row.last_error or ""), row.last_error


@pytest.mark.asyncio
async def test_p0_3_an_invisible_effect_with_a_bound_receipt_completes(tmp_path):
    """4/6: невидимый эффект (API/фон) + НАСТОЯЩАЯ привязанная квитанция -> ВЫПОЛНЕНО.

    Отрицательный контроль к строгости: fail-closed не должен означать «никогда
    не завершается». И тут же контроль к самой квитанции — чужая (выданная на
    другую задачу) не закрывает ничего.
    """
    adapter = FakeAdapter()
    mgr = make_manager(tmp_path / "t.json",
                       FakePlanner([typed(), complete()]),
                       FakeObserver(summary="ok"), adapter=adapter,
                       receipts_of=_receipt_port(adapter))
    state, row = await run(mgr, "оплати счёт")
    assert state is TaskState.COMPLETED, row.last_error

    # Та же цель, тот же эффект, но квитанция выписана на ЧУЖУЮ задачу.
    other = FakeAdapter()
    mgr2 = make_manager(tmp_path / "t2.json",
                        FakePlanner([typed(), complete()] + [complete()] * 30),
                        FakeObserver(summary="ok"), adapter=other,
                        receipts_of=_receipt_port(other, task_id="task_someone_else"))
    state2, row2 = await run(mgr2, "оплати счёт")
    assert state2 is not TaskState.COMPLETED, "чужая квитанция закрыла обязательство"
    assert "квитанции" in (row2.last_error or ""), row2.last_error


@pytest.mark.asyncio
async def test_p0_3_a_browser_effect_with_a_verified_target_state_completes(tmp_path):
    """5/6: эффект в браузере + подтверждённое целевое состояние -> ВЫПОЛНЕНО."""
    observer = screen("settings page, light theme")

    class Navigates(FakeAdapter):
        async def execute(self, a, o):
            if a.kind is ActionKind.BROWSER:
                observer.summary = "settings page, Dark mode enabled"
            return await super().execute(a, o)

    navigate = ComputerAction.make(
        ActionKind.BROWSER, args={"op": "navigate", "url": "https://example.test/settings"},
        expected=ExpectedState(contains_text="settings"))
    mgr = make_manager(tmp_path / "t.json",
                       FakePlanner([navigate, complete_seeing("Dark mode")]),
                       observer, adapter=Navigates())
    t = mgr.create_task('открой в браузере настройки и включи тёмную тему "Dark mode"')
    state = await asyncio.wait_for(mgr.run(t.id), timeout=20)
    assert state is TaskState.COMPLETED, mgr.store.get(t.id).last_error


@pytest.mark.asyncio
async def test_p0_3_one_unknown_obligation_among_verified_ones_still_refuses(tmp_path):
    """6/6: несколько обязательств, ОДНО осталось неизвестным -> НЕ ВЫПОЛНЕНО.

    Остальные подтверждены по-настоящему (файл создан и прочитан с диска) — и
    этого не хватает: улика закрывает только своё обязательство.
    """
    obligations = (FileEffect(path="report.txt", contains="Готово"),
                   UnknownEffect(reason="оплата не названа проверяемо"))

    class WritesTheFile(FakeAdapter):
        async def execute(self, a, o):
            if a.kind is ActionKind.TYPE:
                (tmp_path / "report.txt").write_text("Готово", encoding="utf-8")
            return await super().execute(a, o)

    mgr = make_manager(tmp_path / "t.json",
                       FakePlanner([typed(), complete()] + [complete()] * 30),
                       FakeObserver(summary="ok"), adapter=WritesTheFile(),
                       obligation_probe=file_probe(tmp_path),
                       obligations_of=lambda goal: obligations)
    state, row = await run(mgr, 'создай отчёт report.txt с текстом "Готово" и оплати счёт')
    assert state is not TaskState.COMPLETED
    err = row.last_error or ""
    assert "оплата не названа проверяемо" in err, err
    assert "report.txt: " not in err, "выполненное обязательство не должно значиться невыполненным"
    assert (tmp_path / "report.txt").read_text(encoding="utf-8") == "Готово"


@pytest.mark.asyncio
async def test_p0_3_a_named_file_obligation_without_a_probe_refuses(tmp_path):
    """C3: без порта чтения диска именное файловое обязательство НЕПРОВЕРЯЕМО.

    Раньше оно молча выбрасывалось, и решение падало на слабое правило: хост,
    собравший менеджер без `obligation_probe` (или до того, как порт появился),
    получал AT-01 выключенным без единого события. Непроверяемое — это отказ.
    Файл здесь ДЕЙСТВИТЕЛЬНО создаётся: отказ приходит от невозможности
    прочитать мир независимо, а не от отсутствия эффекта.
    """
    class WritesTheRequestedFile(FakeAdapter):
        async def execute(self, a, o):
            if a.kind is ActionKind.TYPE:
                (tmp_path / "report.txt").write_text("Готово", encoding="utf-8")
            return await super().execute(a, o)

    mgr = make_manager(tmp_path / "t.json",
                       FakePlanner([typed(), complete()] + [complete()] * 30),
                       FakeObserver(summary="ok"), adapter=WritesTheRequestedFile())
    assert mgr.obligation_probe is None
    state, row = await run(mgr, 'создай файл report.txt с текстом "Готово"')
    assert state is not TaskState.COMPLETED
    assert "нечем прочитать" in (row.last_error or ""), row.last_error
    assert (tmp_path / "report.txt").is_file()


def test_p0_3_a_mere_input_act_never_absorbs_a_stronger_promise():
    """Контроль к типизации: механика ввода не «съедает» обещание результата.

    `ActionEffect` закрывается подтверждённым шагом того же рода, поэтому цель,
    обещающая нечто СВЕРХ нажатия, не имеет права им типизироваться: иначе
    «открой сайт и купи билет» закрывался бы одним открытием сайта.
    """
    from bossman.computer_operator.obligations import (ActionEffect, UnverifiableEffect,
                                                       extract_obligations)
    for goal in ("открой сайт и купи билет", "оплати счёт", "save the file",
                 "open the page and pay the invoice", "включи тёмную тему"):
        assert extract_obligations(goal) == (
            UnverifiableEffect(reason="из цели не извлечён проверяемый результат"),), goal
    # А чистая механика ввода типизируется — иначе строгость превратилась бы в
    # «ничего никогда не закрывается».
    for goal in ("click the button", "two clicks", "нажать кнопку", "Открой Блокнот"):
        assert isinstance(extract_obligations(goal)[0], ActionEffect), goal
