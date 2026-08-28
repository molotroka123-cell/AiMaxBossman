"""Автомат состояний браузерной сессии — дословно из спецификации.

Таблица переходов не переписана в код руками: ниже лежит текст
`schemas/browser_state_machine.yaml` как есть, и таблица строится разбором
этого текста. Так расхождение между кодом и спекой невозможно физически, а не
по договорённости.

В отличие от автомата работ (`domain/jobs.py`), здесь **не добавлено ни одного
ребра**. Проверялось, нужны ли они, и оказалось, что нет:

* Личность аккаунта проверяется в `STARTING` (до решения, куда идти) и при
  завершении передачи человеку. У обоих состояний ребро в `STOPPED` уже есть,
  поэтому «при несовпадении личности остановиться» выражается автоматом как он
  написан.
* `LOGIN_REQUIRED` ведёт только в `TAKEOVER_REQUIRED` и `STOPPED` — прямого
  пути `LOGIN_REQUIRED → AUTHENTICATED` в спеке нет. Это читается буквально:
  **вход в аккаунт всегда проходит через человека.** Автоматического входа не
  существует, и это ровно та граница, которую мы и так не собирались
  переходить.

`BUSY` не имеет ребра в `STOPPED`: остановить сессию посреди действия нельзя,
сначала действие должно закончиться хоть чем-нибудь. Тоже оставлено как есть.
"""
from __future__ import annotations

from enum import Enum

# Дословная копия `_staging/social_farm/schemas/browser_state_machine.yaml`.
# Правится только вместе со спецификацией, и тест это сверяет.
SPEC_MACHINE_YAML = """\
states:
  DISABLED: [STARTING]
  STARTING: [LOGIN_REQUIRED, AUTHENTICATED, STOPPED]
  LOGIN_REQUIRED: [TAKEOVER_REQUIRED, STOPPED]
  AUTHENTICATED: [READY, REAUTH_REQUIRED]
  READY: [BUSY, REAUTH_REQUIRED, BROKEN_UI, COOLDOWN, STOPPED]
  BUSY: [READY, TAKEOVER_REQUIRED, BROKEN_UI, COOLDOWN, REAUTH_REQUIRED]
  REAUTH_REQUIRED: [TAKEOVER_REQUIRED, STOPPED]
  TAKEOVER_REQUIRED: [AUTHENTICATED, REAUTH_REQUIRED, STOPPED]
  BROKEN_UI: [READY, STOPPED]
  COOLDOWN: [READY, STOPPED]
  STOPPED: [STARTING]
"""


class BrowserState(str, Enum):
    """Одиннадцать состояний из `browser_session.schema.json`. Перечень закрыт."""

    DISABLED = "DISABLED"
    STARTING = "STARTING"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    AUTHENTICATED = "AUTHENTICATED"
    READY = "READY"
    BUSY = "BUSY"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    TAKEOVER_REQUIRED = "TAKEOVER_REQUIRED"
    BROKEN_UI = "BROKEN_UI"
    COOLDOWN = "COOLDOWN"
    STOPPED = "STOPPED"


def parse_machine(text: str) -> dict[BrowserState, frozenset[BrowserState]]:
    """Разобрать `states:` из текста автомата.

    Разбор намеренно узкий: он понимает ровно ту форму, в которой автомат
    записан в спецификации, и падает на всём остальном. Снисходительный
    разборщик здесь опаснее строгого — он проглотил бы опечатку и построил
    автомат, которого никто не писал.
    """
    table: dict[BrowserState, frozenset[BrowserState]] = {}
    seen_header = False
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not seen_header:
            if line.strip() != "states:":
                raise ValueError(f"ожидался заголовок states:, получено {line!r}")
            seen_header = True
            continue
        if not line.startswith("  ") or ":" not in line:
            raise ValueError(f"неразобранная строка автомата: {line!r}")
        name, _, tail = line.strip().partition(":")
        source = BrowserState(name.strip())
        body = tail.strip()
        if not (body.startswith("[") and body.endswith("]")):
            raise ValueError(f"список переходов должен быть в скобках: {line!r}")
        targets = [t.strip() for t in body[1:-1].split(",") if t.strip()]
        if source in table:
            raise ValueError(f"состояние {source.value} объявлено дважды")
        table[source] = frozenset(BrowserState(t) for t in targets)
    missing = {s for s in BrowserState} - set(table)
    if missing:
        raise ValueError(f"в автомате нет состояний: {sorted(s.value for s in missing)}")
    return table


TRANSITIONS: dict[BrowserState, frozenset[BrowserState]] = parse_machine(SPEC_MACHINE_YAML)

# Состояния, из которых действие выполнять нельзя, пока не вмешается человек.
HUMAN_REQUIRED = frozenset({BrowserState.TAKEOVER_REQUIRED, BrowserState.REAUTH_REQUIRED,
                            BrowserState.LOGIN_REQUIRED})
# Единственное состояние, из которого начинается действие.
ACTIONABLE = frozenset({BrowserState.READY})


class BrowserTransitionError(ValueError):
    """Переход, которого в автомате нет."""


def allowed_transitions(state: BrowserState) -> frozenset[BrowserState]:
    return TRANSITIONS[state]


def can_transition(source: BrowserState, target: BrowserState) -> bool:
    return target in TRANSITIONS[source]


def check_transition(source: BrowserState, target: BrowserState) -> None:
    if not can_transition(source, target):
        allowed = ", ".join(sorted(s.value for s in TRANSITIONS[source])) or "нет"
        raise BrowserTransitionError(
            f"переход {source.value} → {target.value} не разрешён; "
            f"из {source.value} допустимы: {allowed}")


__all__ = ["ACTIONABLE", "HUMAN_REQUIRED", "SPEC_MACHINE_YAML", "TRANSITIONS",
           "BrowserState", "BrowserTransitionError", "allowed_transitions",
           "can_transition", "check_transition", "parse_machine"]
