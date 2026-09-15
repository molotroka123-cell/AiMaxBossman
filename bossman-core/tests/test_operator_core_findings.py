"""Аудит №1 (acceptance 20260906), строки A1-04, A1-06, A1-07, A1-08, A1-10, A1-12.

Каждый тест сначала повторяет ДЕФЕКТ на текущем коде, и рядом стоит контроль,
доказывающий, что законный случай не сломан правкой.
"""
import asyncio
import json
import os

import pytest

from bossman.computer_operator.models import (ActionKind, ComputerAction, ComputerTask,
                                              ExpectedState, Observation, TaskMode, TaskState)
from bossman.computer_operator.policy import ComputerPolicy
from bossman.computer_operator.store import (JsonTaskStore, TaskStoreCorrupt,
                                             TaskStoreUnavailable)
from bossman.computer_operator.verifier import Verifier
from bossman.computer_operator.wiring import FakeAdapter, FakeObserver, FakePlanner, make_manager


def obs(summary="", *, title="", ui_tree=None, sensitive=False):
    return Observation("o", 0.0, {"title": title}, summary, ui_tree, None, sensitive, 0)


def click(**kw):
    kw.setdefault("expected", ExpectedState(contains_text="ok"))
    return ComputerAction.make(ActionKind.CLICK, **kw)


# ---------------------------------------------------- A1-04: вырожденное постусловие

@pytest.mark.parametrize("value", [" ", "\n", "\t ", "a", "."])
def test_degenerate_postcondition_does_not_verify(value):
    """Постусловие из одного пробела/символа совпадало с ЛЮБЫМ экраном."""
    a = ComputerAction.make(ActionKind.COMPLETE, expected=ExpectedState(contains_text=value))
    assert a.expected.is_empty()
    v = Verifier().verify(a, obs("anything at all"))
    assert not v.ok and v.reason == "mutating action missing postcondition"


@pytest.mark.parametrize("value", ["ok", "OK", "Report saved", "  Report saved  "])
def test_real_postcondition_still_verifies(value):
    """Положительный контроль: осмысленное постусловие работает как прежде."""
    a = ComputerAction.make(ActionKind.CLICK, expected=ExpectedState(contains_text=value))
    assert not a.expected.is_empty()
    assert Verifier().verify(a, obs("ok: Report saved to disk")).ok


def test_degenerate_absent_text_does_not_fail_a_good_step():
    """absent_text=" " раньше валил шаг: пробел есть в любой сводке."""
    a = ComputerAction.make(ActionKind.CLICK,
                            expected=ExpectedState(contains_text="saved", absent_text=" "))
    assert Verifier().verify(a, obs("file saved to disk")).ok


def test_degenerate_field_cannot_carry_a_mixed_postcondition():
    """Отрицательный контроль: реальное поле рядом с вырожденным всё ещё проверяется."""
    a = ComputerAction.make(ActionKind.CLICK,
                            expected=ExpectedState(contains_text=" ", window_title_contains="Settings"))
    assert not Verifier().verify(a, obs("anything at all", title="Browser")).ok
    assert Verifier().verify(a, obs("anything at all", title="Settings")).ok


async def test_complete_with_degenerate_postcondition_is_refused(tmp_path):
    """Сквозь менеджер: COMPLETE с постусловием " " не даёт COMPLETED."""
    mgr = make_manager(tmp_path / "t.json",
                       FakePlanner([ComputerAction.make(ActionKind.COMPLETE,
                                                        expected=ExpectedState(contains_text=" "))]),
                       FakeObserver(summary="desktop with a lot of text"))
    t = mgr.create_task("опиши экран", mode=TaskMode.OBSERVE_ONLY)
    t.max_replans = 0
    mgr.store.save(t)
    state = await mgr.run(t.id)
    assert state is TaskState.FAILED
    assert "postcondition" in (mgr.store.get(t.id).last_error or "")


async def test_complete_with_real_postcondition_still_completes(tmp_path):
    """Положительный контроль к предыдущему: наблюдательная цель закрывается."""
    mgr = make_manager(tmp_path / "t.json",
                       FakePlanner([ComputerAction.make(ActionKind.COMPLETE,
                                                        expected=ExpectedState(contains_text="desktop"))]),
                       FakeObserver(summary="desktop with a lot of text"))
    t = mgr.create_task("опиши экран", mode=TaskMode.OBSERVE_ONLY)
    assert await mgr.run(t.id) is TaskState.COMPLETED


# ---------------------------------------------------- A1-06: повреждённый store

def test_corrupt_store_is_quarantined_not_emptied(tmp_path):
    """Нечитаемый tasks.json трактовался как пустой, и следующий save стирал всё."""
    path = tmp_path / "tasks.json"
    store = JsonTaskStore(path)
    old = ComputerTask.create("старая задача")
    store.save(old)
    path.write_text("{oops", encoding="utf-8")

    with pytest.raises(TaskStoreCorrupt):
        store.list()
    quarantine = list(tmp_path.glob("tasks.json.corrupt-*"))
    assert len(quarantine) == 1 and quarantine[0].read_text(encoding="utf-8") == "{oops"

    # После карантина store снова работоспособен, но прежние строки НЕ затёрты
    # молча — они лежат в файле карантина, а не исчезли.
    store.save(ComputerTask.create("новая задача"))
    assert len(store.list()) == 1


def test_unparseable_top_level_is_corrupt(tmp_path):
    """Валидный JSON, но не объект — тоже не «пустой store»."""
    path = tmp_path / "tasks.json"
    store = JsonTaskStore(path)
    store.save(ComputerTask.create("x"))
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(TaskStoreCorrupt):
        store.list()


def test_missing_and_empty_store_are_still_empty(tmp_path):
    """Отрицательный контроль: отсутствие файла — это по-прежнему пустой store."""
    store = JsonTaskStore(tmp_path / "tasks.json")
    assert store.list() == [] and store.get("nope") is None
    (tmp_path / "tasks.json").write_text("", encoding="utf-8")
    assert store.list() == []
    t = ComputerTask.create("x")
    store.save(t)
    assert store.get(t.id).goal == "x"


def test_unreadable_store_is_not_quarantined(tmp_path):
    """Блокировка чтения — не порча: данные целы, уводить файл нельзя."""
    path = tmp_path / "tasks.json"
    store = JsonTaskStore(path)
    store.save(ComputerTask.create("x"))
    original = path.read_text(encoding="utf-8")

    def boom(*a, **kw):
        raise PermissionError(13, "locked")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(type(path), "read_text", boom, raising=False)
        with pytest.raises(TaskStoreUnavailable) as e:
            store.list()
        assert not isinstance(e.value, TaskStoreCorrupt)
    assert path.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob("*.corrupt-*"))


# ---------------------------------------------------- A1-07: внешняя блокировка записи

def test_save_retries_transient_sharing_violation(tmp_path, monkeypatch):
    """os.replace падал на PermissionError с первого раза -> ложный FAILED."""
    store = JsonTaskStore(tmp_path / "tasks.json")
    real = os.replace
    calls = []

    def flaky(src, dst):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(32, "sharing violation")
        return real(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    t = ComputerTask.create("x")
    store.save(t)
    assert len(calls) == 3
    monkeypatch.undo()
    assert store.get(t.id).goal == "x"


def test_permanent_lock_still_raises_and_keeps_revision(tmp_path, monkeypatch):
    """Отрицательный контроль: настоящая блокировка не проглатывается."""
    store = JsonTaskStore(tmp_path / "tasks.json")
    t = ComputerTask.create("x")
    store.save(t)
    held = t.revision

    def locked(src, dst):
        raise PermissionError(32, "sharing violation")

    monkeypatch.setattr(os, "replace", locked)
    with pytest.raises(PermissionError):
        store.save(t)
    # Ревизия не должна уехать вперёд незаписанного снимка: иначе следующая
    # попытка затрёт чужую запись, не получив StaleTaskWrite.
    assert t.revision == held


# ---------------------------------------------------- A1-08: порог уверенности

def test_low_confidence_coordinate_click_denied_whatever_source_says():
    """Модель снимала с себя порог, объявив source="planner"."""
    a = click(args={"x": 10, "y": 10}, confidence=.1, source="planner")
    d = ComputerPolicy().classify(a, mode=TaskMode.CONTROL)
    assert not d.allow and d.reason == "low vision confidence"


def test_declared_vision_source_still_gated():
    """Прежнее поведение сохранено."""
    a = click(confidence=.1, source="vision")
    assert not ComputerPolicy().classify(a, mode=TaskMode.CONTROL).allow


@pytest.mark.parametrize("a", [
    click(args={"x": 10, "y": 10}, confidence=.95, source="planner"),   # уверенная координата
    click(target="Save button", confidence=.1, source="planner"),       # структурная цель, не пиксель
])
def test_legitimate_actions_not_gated(a):
    """Отрицательный контроль: порог не задевает то, что и раньше проходило."""
    assert ComputerPolicy().classify(a, mode=TaskMode.CONTROL).allow


# ---------------------------------------------------- A1-10: чувствительная сводка

def test_sensitive_summary_is_not_written_to_the_journal(tmp_path):
    """Сводка окна банка попадала в tasks.json (и в /computer/tasks) как есть."""
    path = tmp_path / "tasks.json"
    store = JsonTaskStore(path)
    t = ComputerTask.create("x")
    t.last_observation = obs("foreground={'title': 'Sberbank Online - card 4276'}",
                             title="Sberbank Online", sensitive=True)
    store.save(t)
    raw = json.loads(path.read_text(encoding="utf-8"))[t.id]["last_observation"]
    assert "4276" not in raw["summary"] and raw["sensitive"] is True
    assert store.get(t.id).last_observation.summary == "[sensitive observation withheld]"


def test_ordinary_summary_round_trips(tmp_path):
    """Положительный контроль: обычное наблюдение сохраняется дословно."""
    store = JsonTaskStore(tmp_path / "tasks.json")
    t = ComputerTask.create("x")
    t.last_observation = obs("Untitled - Notepad", title="Untitled - Notepad")
    store.save(t)
    assert store.get(t.id).last_observation.summary == "Untitled - Notepad"


# ---------------------------------------------------- A1-12: постусловие по содержимому

PROD_SUMMARY = "foreground={'title': 'Untitled - Notepad'}; ui_tree=available"
PROD_TREE = [{"name": "Report saved", "control_type": "Text", "automation_id": "status"}]


def test_contains_text_matches_structural_tree():
    """Без summarizer summary — это repr окна; постусловие по содержимому
    экрана фейлилось на корректно исполненном шаге и выжигало replan-бюджет."""
    a = click(expected=ExpectedState(contains_text="Report saved"))
    assert Verifier().verify(a, obs(PROD_SUMMARY, title="Untitled - Notepad", ui_tree=PROD_TREE)).ok


def test_text_absent_everywhere_still_fails():
    """Отрицательный контроль: расширение haystack не делает верификацию ручной."""
    a = click(expected=ExpectedState(contains_text="Report saved"))
    assert not Verifier().verify(a, obs(PROD_SUMMARY, ui_tree=[{"name": "Untitled"}])).ok


def test_absent_text_now_sees_the_tree():
    """absent_text от расширения только строже: ошибка в дереве больше не невидима."""
    a = click(expected=ExpectedState(contains_text="notepad", absent_text="Access denied"))
    bad = obs(PROD_SUMMARY, ui_tree=[{"name": "Access denied"}])
    assert not Verifier().verify(a, bad).ok
    assert Verifier().verify(a, obs(PROD_SUMMARY, ui_tree=[{"name": "Report saved"}])).ok


def test_verifier_survives_unserializable_tree():
    """Дерево от чужого провайдера может нести не-JSON объекты."""
    class Node:
        def __repr__(self): return "<node Report saved>"
    a = click(expected=ExpectedState(contains_text="Report saved"))
    assert Verifier().verify(a, obs(PROD_SUMMARY, ui_tree=[Node()])).ok
