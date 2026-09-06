"""S2 (P0) — решение «нужно подтверждение человека» не принадлежит модели.

REPRO: ComputerPolicy.classify выводила «действие последствийное» ТОЛЬКО из
`a.args["semantic"]`, а args — это сырой JSON планировщика (planner.parse_action).
Один и тот же CLICK по цели «Pay 50000 RUB» в TaskMode.CONTROL давал
requires_approval=True при args={"semantic":"pay"} и requires_approval=False
при args={} либо args={"semantic":"click"}. manager._run_loop — единственный
gate, поэтому модель, опустившая или занизившая одно поле, молча проходила
мимо человека для оплаты/деплоя/удаления/смены аккаунта.

Ожидаемое поведение: последствие выводится из НЕ принадлежащих модели улик —
наблюдаемого текста цели/OCR, вида действия и приложения переднего плана;
`semantic` может только УЖЕСТОЧИТЬ решение и никогда не снимает подтверждение.
Обычный безобидный клик при этом остаётся auto.
"""
from __future__ import annotations

import pytest

from bossman.computer_operator.models import (ActionKind, ComputerAction, ExpectedState,
                                              Observation, TaskMode)
from bossman.computer_operator.policy import ComputerPolicy

PAY_TARGET = "Pay 50000 RUB"


def click(**kw):
    return ComputerAction.make(ActionKind.CLICK, expected=ExpectedState(contains_text="ok"), **kw)


def invoke(**kw):
    return ComputerAction.make(ActionKind.UI_INVOKE, expected=ExpectedState(contains_text="ok"), **kw)


def obs(foreground=None, summary="screen"):
    return Observation("obs_1", 0.0, dict(foreground or {}), summary)


# ------------------------------------------------- модель молчит / занижает

def test_omitted_semantic_does_not_skip_approval():
    """REPRO: args={} на «Pay 50000 RUB» давал requires_approval=False."""
    d = ComputerPolicy().classify(click(target=PAY_TARGET, args={}), mode=TaskMode.CONTROL)
    assert d.allow and d.requires_approval, "оплата ушла без подтверждения владельца"


def test_downgraded_semantic_does_not_skip_approval():
    """REPRO: semantic='click' на той же цели снимал подтверждение."""
    d = ComputerPolicy().classify(click(target=PAY_TARGET, args={"semantic": "click"}),
                                  mode=TaskMode.CONTROL)
    assert d.allow and d.requires_approval


@pytest.mark.parametrize("bogus", ["", "click", "noop", "press_button", "safe", "ничего"])
def test_no_value_of_model_semantic_can_de_escalate(bogus):
    """`semantic` — только эскалация: ни одно значение не снимает подтверждение,
    которое уже выведено из улик."""
    d = ComputerPolicy().classify(click(target=PAY_TARGET, args={"semantic": bogus}),
                                  mode=TaskMode.CONTROL)
    assert d.requires_approval, f"semantic={bogus!r} снял подтверждение"


def test_model_supplied_requires_approval_flag_is_ignored():
    d = ComputerPolicy().classify(
        click(target=PAY_TARGET, args={"semantic": "click", "requires_approval": False}),
        mode=TaskMode.CONTROL)
    assert d.requires_approval


# ---------------------------------------------- улики: наблюдаемый текст цели

@pytest.mark.parametrize("target", [
    "Pay 50000 RUB", "Оплатить 50 000 ₽", "Confirm purchase", "Transfer funds",
    "Удалить аккаунт", "Delete all files", "Deploy to production", "Публикация релиза",
    "Uninstall application", "Change password", "Отключить брандмауэр",
])
def test_consequence_detected_from_observed_target_text(target):
    d = ComputerPolicy().classify(click(target=target, args={}), mode=TaskMode.CONTROL)
    assert d.allow and d.requires_approval, f"{target!r} прошло без подтверждения"
    assert d.approval_kind and d.approval_kind.startswith("computer_")


def test_consequence_detected_for_ui_invoke_too():
    d = ComputerPolicy().classify(invoke(target="Удалить аккаунт", args={}), mode=TaskMode.CONTROL)
    assert d.allow and d.requires_approval


def test_consequence_detected_from_foreground_application():
    """Приложение переднего плана — улика наблюдателя, а не поле модели."""
    d = ComputerPolicy().classify(
        click(target="Continue", args={}), mode=TaskMode.CONTROL,
        observation=obs(foreground={"app": "Sberbank Online", "title": "Перевод средств"}))
    assert d.allow and d.requires_approval


# ------------------------------------------------------- безобидное остаётся auto

@pytest.mark.parametrize("target", [
    "Notepad", "File", "Открыть", "Scroll down", "Tab 2", "OK", "Close window",
])
def test_ordinary_benign_click_stays_auto(target):
    d = ComputerPolicy().classify(click(target=target, args={}), mode=TaskMode.CONTROL)
    assert d.allow and not d.requires_approval, f"{target!r} без нужды требует подтверждения"


def test_benign_click_with_benign_observation_stays_auto():
    d = ComputerPolicy().classify(
        click(target="Notepad", args={}), mode=TaskMode.CONTROL,
        observation=obs(foreground={"app": "notepad.exe", "title": "Untitled - Notepad"}))
    assert d.allow and not d.requires_approval


def test_benign_typing_stays_auto():
    a = ComputerAction.make(ActionKind.TYPE, expected=ExpectedState(contains_text="ok"),
                            text="hello world")
    d = ComputerPolicy().classify(a, mode=TaskMode.CONTROL)
    assert d.allow and not d.requires_approval


# ---------------------------------------- эскалация моделью по-прежнему работает

def test_model_semantic_still_escalates_without_any_other_evidence():
    """Если модель сама назвала действие последствийным — верим и спрашиваем."""
    d = ComputerPolicy().classify(click(target="Button 3", args={"semantic": "pay"}),
                                  mode=TaskMode.CONTROL)
    assert d.allow and d.requires_approval and d.approval_kind == "computer_pay"


def test_denies_still_win_over_the_new_approval_path():
    """Новый путь не должен превращать deny в «спросим» (обход запрета)."""
    p = ComputerPolicy()
    d = p.classify(click(target="BOSSMAN Approvals: Pay 50000 RUB", args={}), mode=TaskMode.CONTROL)
    assert not d.allow and not d.requires_approval
    d2 = p.classify(click(target=PAY_TARGET, args={}), mode=TaskMode.OBSERVE_ONLY)
    assert not d2.allow
    d3 = p.classify(click(target=PAY_TARGET, args={}, source="vision", confidence=0.1),
                    mode=TaskMode.CONTROL)
    assert not d3.allow


# ----------------------------------------------- gate манагера видит решение

async def test_manager_asks_owner_when_model_omits_semantic(tmp_path):
    """Сквозная проверка: тот же CLICK без `semantic` доходит до approval-хука."""
    from bossman.computer_operator.models import TaskState
    from bossman.computer_operator.store import JsonTaskStore
    from bossman.computer_operator.manager import ComputerOperatorManager
    from bossman.computer_operator.wiring import FakeAdapter, FakeObserver

    created: list[str] = []

    class _Planner:
        def __init__(self, actions): self.actions = list(actions)
        async def next_action(self, **kw):
            return self.actions.pop(0)

    async def approval_create(kind, preview, **kw):
        created.append(kind)
        return len(created)

    async def approval_wait(aid):
        return {"status": "rejected"}

    from bossman.computer_operator.adapters.router import ActionRouter
    adapter = FakeAdapter()
    mgr = ComputerOperatorManager(
        store=JsonTaskStore(tmp_path / "tasks.json"),
        planner=_Planner([click(target=PAY_TARGET, args={})]),
        observer=FakeObserver(), action_router=ActionRouter([adapter]),
        approval_create=approval_create, approval_wait=approval_wait,
        event_emit=lambda *a, **k: None)
    t = mgr.create_task("pay the invoice")
    await mgr.run(t.id)
    assert created, "оплата выполнена без запроса подтверждения"
    assert adapter.executed == []
    assert mgr.store.get(t.id).state is TaskState.FAILED
