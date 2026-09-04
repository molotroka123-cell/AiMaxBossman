"""Feature — Action Capability Router (universal action-execution contract, MODULE 1).

BCC-V2-UNIVERSAL-ACTION-EXECUTION-P1-001.

Продолжение BCC-V2-SESSION-20783913FA36-P1-FIX-001 (см. bcc/features/action_gate.py):
тот патч не даёт ложному текстовому «успеху» просочиться в completed, но САМ
по себе не заставляет систему реально выполнить действие — агент в сессии
20783913fa36 отвечал текстом именно потому, что `agents.tools` пуст по
умолчанию (MVP: «опасных инструментов нет», bcc/db.py), и запрошенное действие
(browser.*) никогда не попадало в tool_schemas этого run'а.

Это ДЕТЕРМИНИРОВАННЫЙ (без LLM) `before_run`-хук: по тексту задачи узнаёт явный
запрос действия над браузером/компьютером владельца и, если задача ещё не
сконфигурирована явно (`meta.allowed_tools` не задан скиллом/миссией/владельцем),
прикрепляет уже существующий набор инструментов bcc/features/tools_browser.py к
ЭТОМУ run'у — через тот же приоритетный канал, что и раньше (`allowed_tools_for`:
`tasks.meta.allowed_tools` важнее `agents.tools`). Ни агент, ни его дефолтный
набор прав не меняются — это НЕ повышение прав агента навсегда, а разовое
разрешение для одной задачи, которое всё равно проходит штатную политику
AUTO/ASK/DENY каждого инструмента (bcc.tools.decide_effect) в момент вызова.

Второй кусок универсального инварианта
(`SIDE_EFFECT_REQUIRED && VERIFIED_SIDE_EFFECT == false → COMPLETED == false`)
для браузера: когда из текста задачи можно детерминированно вывести целевой
домен (например «YouTube» → youtube.com), хук ТАКЖЕ прикрепляет
`meta.review.evidence` — структурированное ожидание kind="browser" для уже
готового верификатора (bcc/v2/verification._observe_browser + review_gate),
если владелец не настроил review сам. Это переиспользование существующего
конвейера свежих доказательств (F-012), а не новый: browser tool-calls сам по
себе НЕ значит «страница открыта, поиск выполнен, воспроизведение началось» —
`_has_any_tool_call` в action_gate.py умышленно слабая проверка («хоть
что-то из tool-пути»), а review_gate с evidence — сильная («заново
пересня́тый URL/заголовок реально совпадает с ожиданием»). Не удалось вывести
домен — evidence не прикрепляется: система не изобретает несуществующее
ожидание, честно остаётся на слабой проверке action_gate.

Что этот файл НЕ делает:
  * не исполняет браузер сам — только настраивает существующий tool-loop
    (bcc/engine.py) и существующий verified-evidence конвейер
    (bcc/v2/verification.py) на эту одну задачу;
  * не создаёт вторую браузерную реализацию;
  * не меняет права агента навсегда и не обходит AUTO/ASK/DENY;
  * не классифицирует ничего, кроме браузерных действий (MODULE 1) — остальные
    23 модуля спецификации сознательно не тронуты механически (см. коммит).
"""
from __future__ import annotations

import re

import sqlalchemy as sa

from ..db import tasks as tasks_t, utcnow
from . import Feature

# Явные глаголы действия (EN + RU), как того требует спецификация п.1 —
# семантические эквиваленты open/launch/click/type/search/play/download/
# upload/navigate/close/switch/interact.
# ВАЖНО: без (?iu) внутри — флаги задаются один раз, при компиляции _ACTION_RE
# ниже; эти три паттерна используются только как строки-фрагменты внутри неё
# (Python re запрещает встроенный (?iu) не в начале составного выражения).
_ACTION_VERB_PAT = (
    r"\b(open|launch|start|click|type|search|play|download|upload|navigate|"
    r"close|switch)\b|"
    r"открой|открыть|запусти|запустить|включи|включить|нажми|нажать|введи|ввести|"
    r"найди\b|найти\b|скачай|скачать|загрузи|загрузить|перейди|перейти|закрой|закрыть|"
    r"переключ")

# Тема «браузер/сайт/устройство владельца» — без неё «open»/«click» слишком общее
# (см. окно проверки ниже: команда должна быть об одном и том же предмете).
_BROWSER_TOPIC_PAT = (
    r"\bbrowser\b|\bchrome\b|\bfirefox\b|\byoutube\b|\bwebsite\b|\bwebsit\w*\b|\burl\b|"
    r"[a-z0-9-]+\.(?:com|org|net|io|dev|co|ru)\b|"
    r"браузер\w*|са[йи]т\w*|ютуб\w*|youtube")

_MY_COMPUTER_PAT = (
    r"\bmy (computer|pc|machine|browser)\b|"
    r"на моём компьютере|на моем компьютере|на своём компьютере|на своем компьютере|"
    r"на компьютере|мой компьютер|моём браузере|моем браузере")

# Глагол и тема должны относиться к одной фразе (то же окно, что и в
# action_gate._REFUSAL_RE) — иначе «найди файл» + случайное упоминание сайта
# страницей ниже ложно классифицировались бы как действие над браузером.
_ACTION_RE = re.compile(
    rf"({_ACTION_VERB_PAT})[^.!?\n]{{0,80}}({_BROWSER_TOPIC_PAT}|{_MY_COMPUTER_PAT})|"
    rf"({_BROWSER_TOPIC_PAT}|{_MY_COMPUTER_PAT})[^.!?\n]{{0,80}}({_ACTION_VERB_PAT})",
    re.I | re.U)

CAPABILITY_BROWSER = "BROWSER_ACTION"

# Полный набор УЖЕ РЕГИСТРИРОВАННЫХ (bcc/features/tools_browser.py) инструментов —
# вторая браузерная реализация не создаётся, только выдаётся доступ к готовой.
BROWSER_TOOLS = ("browser.open", "browser.read_dom", "browser.screenshot",
                 "browser.click", "browser.type", "browser.select",
                 "browser.back", "browser.reload", "browser.submit", "browser.login")

# Известные домены по ключевому слову задачи — детерминированный, закрытый
# список (без LLM и без угадывания произвольных сайтов: см. докстринг модуля).
_KNOWN_DOMAINS = (
    (re.compile(r"(?iu)youtube|ютуб"), "youtube.com"),
)
_EXPLICIT_DOMAIN_RE = re.compile(r"(?iu)\b([a-z0-9][a-z0-9-]*\.(?:com|org|net|io|dev|co|ru))\b")


def classify(prompt: str) -> str | None:
    """Детерминированная классификация запроса действия над браузером/компьютером
    владельца. Проверяется ТЕКСТ ЗАДАЧИ (в отличие от action_gate, который
    смотрит на ответ модели) — это разные, дополняющие друг друга проверки."""
    text = prompt or ""
    return CAPABILITY_BROWSER if _ACTION_RE.search(text) else None


def target_domain(prompt: str) -> str | None:
    """Домен, который можно детерминированно вывести из текста задачи, для
    автоматического evidence kind="browser" (url_contains). Известное
    название сайта → домен из закрытого списка; иначе — явный домен,
    буквально упомянутый в тексте. Ничего не найдено — None (см. докстринг:
    несуществующее ожидание не изобретается)."""
    text = prompt or ""
    for pattern, domain in _KNOWN_DOMAINS:
        if pattern.search(text):
            return domain
    m = _EXPLICIT_DOMAIN_RE.search(text)
    return m.group(1).lower() if m else None


async def _set_meta(svc, task_id: int, meta: dict) -> None:
    async with svc.db.session() as s:
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(
            meta=meta, updated_at=utcnow()))
        await s.commit()


async def _before_run(svc):
    async def before_run(task, run):
        meta = task.get("meta") if isinstance(task.get("meta"), dict) else {}
        capability = classify(task.get("prompt") or "")
        if capability != CAPABILITY_BROWSER:
            return None

        changed = False
        new_meta = dict(meta)

        if "allowed_tools" not in meta:
            # Приоритет `tasks.meta.allowed_tools` над `agents.tools` уже есть
            # в bcc.tools.allowed_tools_for — здесь только используем канал,
            # ничего в нём не меняем.
            new_meta["allowed_tools"] = list(BROWSER_TOOLS)
            changed = True

        domain = target_domain(task.get("prompt") or "")
        if "review" not in meta and domain:
            # Готовый verified-evidence конвейер (F-012, bcc/v2/verification +
            # review_gate) вместо нового: kind="browser" уже реализован и
            # уже покрыт тестами review_gate — переиспользуем, не дублируем.
            new_meta["review"] = {
                "reviewer_agent_id": None, "criteria": "",
                "evidence": [{"kind": "browser", "target": "session",
                             "expect": {"url_contains": domain}}],
                "max_review_retries": 2,
            }
            changed = True

        if not changed:
            return None

        router_meta = dict(new_meta.get("action_router") or {})
        router_meta.update({"capability": capability, "target_domain": domain})
        new_meta["action_router"] = router_meta

        await _set_meta(svc, task["id"], new_meta)
        # Тот же объект `task`, что использует _run() ниже для allowed_tools_for
        # и (через meta.review) review_gate — мутация на месте нужна, чтобы
        # решение подействовало на ЭТОТ прогон, а не только на следующий.
        task["meta"] = new_meta
        await svc.bus.emit("action_router.capability_selected", task_id=task["id"],
                           capability=capability, target_domain=domain or "",
                           tools=list(BROWSER_TOOLS))
        return None
    return before_run


async def _setup(svc):
    svc.engine.add_hook("before_run", await _before_run(svc))


FEATURE = Feature(name="action_router", setup=_setup)
