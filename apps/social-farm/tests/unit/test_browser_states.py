"""Автомат браузерной сессии обязан совпадать со спецификацией дословно.

Наша копия автомата лежит в коде текстом (`SPEC_MACHINE_YAML`), и таблица
переходов строится её разбором. Это защищает от расхождения между таблицей и
текстом, но не от расхождения текста со спецификацией — а именно оно и опасно:
лишнее ребро в автомате означает состояние, в которое сессия попадёт, а никто
этого не планировал.

Поэтому источник берётся не «рядом», а из пакета спецификации, лежащего в
репозитории: сначала распакованный `_staging/`, если он есть, иначе прямо из
`BOSSMAN_SOCIAL_FARM_APP4_TECH_SPEC_V1_1.zip`. Тест, который молча
пропускается, ничего не охраняет.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from social_farm.browser import (SPEC_MACHINE_YAML, BrowserState,
                                 BrowserTransitionError, allowed_transitions,
                                 can_transition, check_transition)
from social_farm.browser.states import ACTIONABLE, HUMAN_REQUIRED, parse_machine

REPO = Path(__file__).resolve().parents[4]
SPEC_DIR = REPO / "_staging" / "social_farm"
SPEC_ZIP = REPO / "BOSSMAN_SOCIAL_FARM_APP4_TECH_SPEC_V1_1.zip"
RELATIVE = "schemas/browser_state_machine.yaml"


def spec_machine_text() -> str | None:
    """Текст автомата из спецификации, откуда бы она ни была доступна."""
    unpacked = SPEC_DIR / RELATIVE
    if unpacked.exists():
        return unpacked.read_text(encoding="utf-8")
    if SPEC_ZIP.exists():
        with zipfile.ZipFile(SPEC_ZIP) as archive:
            if RELATIVE in archive.namelist():
                return archive.read(RELATIVE).decode("utf-8")
    return None


def test_the_specification_is_reachable_from_the_repository():
    """Если это упало, все проверки ниже перестали что-либо доказывать."""
    assert spec_machine_text() is not None, (
        f"ни {SPEC_DIR / RELATIVE}, ни {SPEC_ZIP} не читаются")


def test_our_copy_matches_the_specification_word_for_word():
    text = spec_machine_text()
    assert text is not None
    assert text.strip() == SPEC_MACHINE_YAML.strip()


def test_the_table_is_built_from_the_specification_text_itself():
    """Таблица не переписана руками: она разбирается из того же текста."""
    text = spec_machine_text()
    assert text is not None
    assert parse_machine(text) == parse_machine(SPEC_MACHINE_YAML)


def test_every_state_of_the_schema_is_in_the_machine():
    """Одиннадцать состояний `browser_session.schema.json`. Перечень закрыт."""
    assert len(BrowserState) == 11
    for state in BrowserState:
        assert isinstance(allowed_transitions(state), frozenset)


@pytest.mark.parametrize("source,target", [
    (BrowserState.DISABLED, BrowserState.STARTING),
    (BrowserState.STARTING, BrowserState.LOGIN_REQUIRED),
    (BrowserState.READY, BrowserState.BUSY),
    (BrowserState.BUSY, BrowserState.TAKEOVER_REQUIRED),
    (BrowserState.TAKEOVER_REQUIRED, BrowserState.AUTHENTICATED),
    (BrowserState.STOPPED, BrowserState.STARTING),
])
def test_transitions_the_specification_allows(source, target):
    check_transition(source, target)
    assert can_transition(source, target)


@pytest.mark.parametrize("source,target,why", [
    (BrowserState.LOGIN_REQUIRED, BrowserState.AUTHENTICATED,
     "автоматического входа не существует: вход всегда завершает человек"),
    (BrowserState.LOGIN_REQUIRED, BrowserState.READY, "то же самое, короче"),
    (BrowserState.BUSY, BrowserState.STOPPED,
     "остановить сессию посреди действия нельзя: сначала действие обязано "
     "закончиться хоть чем-нибудь"),
    (BrowserState.DISABLED, BrowserState.READY,
     "мимо запуска и проверки личности в работу не попадают"),
    (BrowserState.BROKEN_UI, BrowserState.BUSY,
     "из поломанного интерфейса нельзя сразу в действие"),
    (BrowserState.COOLDOWN, BrowserState.BUSY,
     "пауза на то и пауза"),
])
def test_transitions_the_specification_forbids(source, target, why):
    assert not can_transition(source, target), why
    with pytest.raises(BrowserTransitionError):
        check_transition(source, target)


def test_no_edge_was_added_to_the_specification_machine():
    """В отличие от автомата работ, сюда не добавлено ни одного ребра."""
    ours = parse_machine(SPEC_MACHINE_YAML)
    theirs = parse_machine(spec_machine_text() or SPEC_MACHINE_YAML)
    extra = {state.value: sorted(t.value for t in ours[state] - theirs[state])
             for state in ours if ours[state] - theirs[state]}
    assert not extra, f"рёбра, которых нет в спецификации: {extra}"


def test_states_that_wait_for_a_human_are_not_actionable():
    assert ACTIONABLE == frozenset({BrowserState.READY})
    assert HUMAN_REQUIRED == frozenset({BrowserState.TAKEOVER_REQUIRED,
                                        BrowserState.REAUTH_REQUIRED,
                                        BrowserState.LOGIN_REQUIRED})
    assert not (ACTIONABLE & HUMAN_REQUIRED)


def test_a_lenient_parser_would_be_more_dangerous_than_a_strict_one():
    """Разборщик обязан падать на всём, что не является автоматом спецификации."""
    with pytest.raises(ValueError):
        parse_machine("состояния:\n  DISABLED: [STARTING]\n")
    with pytest.raises(ValueError):
        parse_machine("states:\n  DISABLED: STARTING\n")
    with pytest.raises(ValueError):
        parse_machine("states:\n  DISABLED: [STARTING]\n")     # остальных нет
    with pytest.raises(ValueError):
        parse_machine(SPEC_MACHINE_YAML + "  DISABLED: [STOPPED]\n")
