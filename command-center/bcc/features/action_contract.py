"""Feature — Universal Action-Execution Contract (BCC-V2-UNIVERSAL-ACTION-EXECUTION-P1-001).

Продолжение MODULE 1 (bcc/features/action_router.py, browser — НЕ трогается
этим файлом, см. его собственный докстринг). Тот же инвариант,

    SIDE_EFFECT_REQUIRED && VERIFIED_SIDE_EFFECT == FALSE → TASK_SUCCESS == FALSE

для остальных существующих исполнителей действий V2: terminal (+ файлы,
которые в этой кодовой базе создаются именно через terminal.run — отдельного
инструмента filesystem.write нет), apps (MODULE 3, bcc/features/tools_apps.py),
memory, openclaw, code (opencode.send + terminal), github (git через
terminal.run), mcp, plugin. Для capability, у которой в реестре инструментов
вообще нет ни одного (images/workflow/schedules — см. ниже), это ОДИН И ТОТ
ЖЕ файл честно возвращает CAPABILITY_UNAVAILABLE вместо попытки притвориться,
что инструмент есть.

Архитектурное решение по прямому требованию задачи: ОДИН общий
capability/action-contract слой (эта пара хуков), а не по отдельному
регекс-роутеру на каждый модуль. Общее:

  * ОДНА таблица `CAPABILITIES` (данные, не код) — про каждую capability
    известны только (а) как её узнать по тексту задачи, (б) какое
    `bcc.tools.ToolSpec.source` семейство инструментов является для неё
    исполнителем, (в) необязательный вывод evidence для уже готового
    verified-evidence конвейера (bcc/v2/verification.py + review_gate, F-012);
  * ОДИН before_run: классификация → прикрепление инструментов семейства через
    тот же приоритетный канал `tasks.meta.allowed_tools` (bcc.tools.
    allowed_tools_for), что уже использует action_router.py, → (когда возможно)
    прикрепление evidence;
  * ОДИН gate_completion: классификация ЗАНОВО (не зависит от того, отработал
    ли before_run на этом же объекте `task`) → был ли в ЭТОМ run'е хоть один
    tool_calls СЕМЕЙСТВА этой capability (bcc.db.tool_calls.source; статус не
    важен — «хоть какая-то попытка инструментального пути», ровно та же
    качественная граница, что action_gate.py уже проводит для browser) → нет
    ни одного и семейство вообще существует в реестре → одна попытка
    самокоррекции с фидбеком, иначе честный терминал `failed`; семейства НЕТ в
    реестре вовсе (apps выключен флагом ДО регистрации? нет — ToolSpec
    регистрируется всегда, если модуль загружен; но images/workflow/schedules
    сейчас не имеют ToolSpec вообще) → сразу `failed` с capability_unavailable,
    без бессмысленного повтора.

Что этот файл НЕ делает (важно, чтобы не «переоткрывать» уже сделанное и не
плодить лишнее):
  * не трогает bcc/features/action_router.py и bcc/features/action_gate.py —
    браузер (MODULE 1) уже полностью реализован своим собственным, отдельно
    протестированным механизмом, повторно не классифицируется здесь;
  * не строит новый исполнитель — использует УЖЕ существующие
    tools_terminal.py, tools_memory.py, tools_openclaw.py, tools_opencode.py,
    tools_mcp.py (динамическая регистрация с source="mcp"), plugins.py
    (source="plugin"), и (MODULE 3 этой же задачи) новый тонкий
    bcc/features/tools_apps.py — обёртку над уже готовым apps_control.py;
    для images/workflow/schedules(missions) исполнителя, вызываемого моделью,
    сейчас НЕТ вообще — это честно отражено пустым `tool_sources`, а не
    придумано;
  * не меняет AUTO/ASK/DENY, governor, restart/dedup — прикреплённые
    инструменты по-прежнему проходят bcc.tools.decide_effect на каждый вызов
    без исключений;
  * не подделывает verified evidence, если её нельзя вывести детерминированно
    из текста задачи (тот же принцип, что action_router.target_domain: не
    нашли — не изобретаем, остаёмся на более слабой проверке «была хоть
    одна попытка»).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

import sqlalchemy as sa

from ..db import agents as agents_t, tasks as tasks_t, tool_calls as tool_calls_t, utcnow
from ..tools import REGISTRY, allowed_tools_for
from ..v2.verification import ExpectedState
from . import Feature

META_KEY = "action_contract_attempts"
RULE = "no_verified_action"


@dataclass(frozen=True)
class Capability:
    name: str
    pattern: "re.Pattern[str]"
    tool_sources: frozenset[str]      # bcc.tools.ToolSpec.source семейства-исполнителя
    evidence: Callable[[str], ExpectedState | None] | None = None


def _clause_re(verb_pat: str, topic_pat: str) -> "re.Pattern[str]":
    """Тот же приём, что и action_router._ACTION_RE: глагол и тема — в одном
    предложении (окно ~80 знаков, любой порядок), без встроенных (?iu) внутри
    фрагментов (Python re требует их только в начале целого выражения)."""
    return re.compile(
        rf"({verb_pat})[^.!?\n]{{0,80}}({topic_pat})|({topic_pat})[^.!?\n]{{0,80}}({verb_pat})",
        re.I | re.U)


# ------------------------------------------------------------- verb-фрагменты

_DO_VERB = (r"\b(run|execute|create|make|delete|remove|save)\b|"
           r"запусти\w*|выполни\w*|создай\w*|удали\w*|сохрани\w*|сделай\w*")
_OPEN_VERB = r"\b(open|launch|start|close|switch)\b|открой\w*|запусти\w*|включи\w*|закрой\w*|переключ\w*"
_FIX_VERB = r"\b(fix|repair|implement|write)\b|исправь\w*|почини\w*|напиши\w*"
_PUSH_VERB = r"\b(push|commit|create)\b|запушь\w*|закоммить\w*|закомить\w*|создай\w*"
_SEND_VERB = r"\b(send)\b|отправь\w*|напиши\w*"
_USE_VERB = r"\b(use|call|invoke|via)\b|используй\w*|вызови\w*|через"
_GEN_VERB = r"\b(generate|create|draw|edit)\b|сгенерируй\w*|нарисуй\w*|создай\w*|отредактируй\w*"

# MEMORY и SCHEDULES: глагол в русском фактически САМ несёт тему («запомни»/
# «напомни» не нуждаются в отдельном слове-теме рядом) — clause_re здесь
# требовал бы ДВУХ разных вхождений в тексте и не совпадал бы с настоящими
# формулировками владельца («Запомни это», «Напомни мне завтра»).
_MEMORY_RE = re.compile(
    r"\bremember\b|\bnote\s+that\b|запомни\w*|сохрани\w*\s+в\s+памят\w*", re.I | re.U)
_SCHEDULE_RE = re.compile(
    r"\bremind\s+me\b|\bschedule\s+(a|this)\b|напомни\w*|запланируй\w*|"
    r"создай\w*[^.!?\n]{0,40}миссию|create\s+a\s+mission", re.I | re.U)

# ------------------------------------------------------------ topic-фрагменты

_TERMINAL_TOPIC = (r"\bterminal\b|\bshell\b|\bcommand\b|\bscript\b|"
                   r"терминал\w*|команд\w*|консол\w*|скрипт\w*")
_FILE_TOPIC = (r"\bfile\b|\bfolder\b|\bdirectory\b|"
              r"файл\w*|папк\w*|директор\w*")
_APP_TOPIC = (r"\bapplication\b|\bapp\b|\bcalculator\b|\bnotepad\b|"
             r"приложени\w*|калькулятор\w*|блокнот\w*")
_OPENCLAW_TOPIC = (r"\bmessage\b|\bchannel\b|\bchat\b|"
                   r"сообщени\w*|канал\w*|чат\w*")
_CODE_TOPIC = (r"\bbug\b|\bcode\b|\bcode\s*base\b|"
              r"баг\w*|код[ае]?\b|ошибк\w*")
_GITHUB_TOPIC = (r"\bgit\b|\bgithub\b|\bpull\s*request\b|\bpr\b|"
                 r"гит\b|коммит\w*|пуш\w*|pull[- ]?request\w*")
_MCP_TOPIC = r"\bmcp\b"
_PLUGIN_TOPIC = r"\bplugin\b|плагин\w*"
_IMAGE_TOPIC = r"\bimage\b|\bpicture\b|картинк\w*|изображени\w*|рисун\w*"
_WORKFLOW_TOPIC = r"\bworkflow\b|воркфлоу\w*|рабочий\s+процесс\w*"

_TERMINAL_FILE_RE = re.compile(
    rf"({_DO_VERB})[^.!?\n]{{0,80}}({_TERMINAL_TOPIC}|{_FILE_TOPIC})|"
    rf"({_TERMINAL_TOPIC}|{_FILE_TOPIC})[^.!?\n]{{0,80}}({_DO_VERB})", re.I | re.U)

_FILENAME_RE = re.compile(r"\b[\w][\w./-]{0,80}\.[a-zA-Z0-9]{1,8}\b")


def _terminal_evidence(prompt: str) -> ExpectedState | None:
    """Явное имя файла в тексте задачи (закрытый, детерминированный вывод —
    как action_router.target_domain: не нашли — не изобретаем)."""
    m = _FILENAME_RE.search(prompt or "")
    if not m:
        return None
    return ExpectedState(kind="file", target=m.group(0), expect={"exists": True})


# Порядок — приоритет из задания (1..12), browser (MODULE 1) исключён намеренно.
CAPABILITIES: tuple[Capability, ...] = (
    Capability("TERMINAL_FILE_ACTION", _TERMINAL_FILE_RE, frozenset({"terminal"}),
              evidence=_terminal_evidence),
    Capability("APPS_ACTION", _clause_re(_OPEN_VERB, _APP_TOPIC), frozenset({"apps"})),
    Capability("CODE_ACTION", _clause_re(_FIX_VERB, _CODE_TOPIC),
              frozenset({"opencode", "terminal"})),
    Capability("GITHUB_ACTION", _clause_re(_PUSH_VERB, _GITHUB_TOPIC), frozenset({"terminal"})),
    Capability("MCP_ACTION", _clause_re(_USE_VERB, _MCP_TOPIC), frozenset({"mcp"})),
    Capability("OPENCLAW_ACTION", _clause_re(_SEND_VERB, _OPENCLAW_TOPIC), frozenset({"openclaw"})),
    Capability("MEMORY_ACTION", _MEMORY_RE, frozenset({"memory"})),
    Capability("IMAGES_ACTION", _clause_re(_GEN_VERB, _IMAGE_TOPIC), frozenset()),
    Capability("WORKFLOW_ACTION", _clause_re(_OPEN_VERB, _WORKFLOW_TOPIC), frozenset()),
    Capability("SCHEDULES_ACTION", _SCHEDULE_RE, frozenset()),
    Capability("PLUGIN_ACTION", _clause_re(_USE_VERB, _PLUGIN_TOPIC), frozenset({"plugin"})),
)


def classify(prompt: str) -> Capability | None:
    """Первая подошедшая capability по приоритету задания. Проверяется ТЕКСТ
    ЗАДАЧИ (симметрично action_router.classify), не ответ модели."""
    text = prompt or ""
    for cap in CAPABILITIES:
        if cap.pattern.search(text):
            return cap
    return None


# --------------------------------------------------------------- инфраструктура

def _family_tool_names(tool_sources: frozenset[str]) -> list[str]:
    return [t.name for t in REGISTRY.all()
            if t.source in tool_sources and t.category != "read"]


async def _has_family_tool_call(svc, run_id: int, tool_sources: frozenset[str]) -> bool:
    """Хотя бы один УСПЕШНО исполненный (status="executed") вызов инструмента
    СЕМЕЙСТВА этой capability в этом run'е.

    Сознательно строже, чем «любой исход» у action_gate.py (browser, MODULE 1,
    не трогается этим файлом): там «любой исход» оправдан узкой ролью —
    отличить настоящую попытку инструментального пути от нуля попыток, а
    глубокую проверку берёт на себя review_gate по evidence. Здесь evidence
    прикрепляется НЕ для каждой capability (см. `Capability.evidence` — для
    большинства это `None`, честно, а не выдумано), поэтому единственная линия
    защиты для них — сам факт исполнения. «Отклонено политикой»/«ошибка
    инструмента» — это НЕ «инструментальный путь пройден», это ровно то, что
    критическое правило задания называет прямо: инструмент попытался и не
    смог — не является доказательством, что запрошенный эффект наступил."""
    if not tool_sources:
        return False
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(tool_calls_t.c.id).where(sa.and_(
            tool_calls_t.c.run_id == run_id,
            tool_calls_t.c.source.in_(tool_sources),
            tool_calls_t.c.status == "executed")).limit(1))).first()
    return row is not None


async def _agent_has_family_tools(svc, task: dict, tool_sources: frozenset[str]) -> bool:
    names = set(_family_tool_names(tool_sources))
    if not names:
        return False
    agent_id = task.get("agent_id")
    if agent_id is None:
        return False
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(agents_t).where(agents_t.c.id == agent_id))).first()
    if row is None:
        return False
    agent = dict(row._mapping)
    return bool(names & set(allowed_tools_for(task, agent)))


async def _meta(svc, task_id: int) -> dict:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(tasks_t.c.meta).where(tasks_t.c.id == task_id))).first()
    return dict(row._mapping["meta"]) if row and isinstance(row._mapping["meta"], dict) else {}


async def _set_meta(svc, task_id: int, meta: dict) -> None:
    async with svc.db.session() as s:
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(
            meta=meta, updated_at=utcnow()))
        await s.commit()


async def _attempts(svc, task_id: int) -> tuple[int, dict]:
    meta = await _meta(svc, task_id)
    return int(meta.get(META_KEY, 0)), meta


async def _bump_attempts(svc, task_id: int, meta: dict) -> None:
    meta = dict(meta)
    meta[META_KEY] = int(meta.get(META_KEY, 0)) + 1
    await _set_meta(svc, task_id, meta)


def _verdict(verdict: str, *, rule: str = "", feedback: str = "", requeue: bool = True,
            status: str = "") -> dict:
    out: dict = {"verdict": verdict}
    if rule:
        out["reasons"] = f"action_contract/{rule}"
    if verdict == "FAIL":
        out["requeue"] = requeue
        if feedback:
            out["feedback"] = feedback
    if status:
        out["status"] = status
    return out


# -------------------------------------------------------------------- before_run

async def _before_run(svc):
    async def before_run(task, run):
        cap = classify(task.get("prompt") or "")
        if cap is None:
            return None
        meta = task.get("meta") if isinstance(task.get("meta"), dict) else {}
        changed = False
        new_meta = dict(meta)

        family = _family_tool_names(cap.tool_sources)
        if family and "allowed_tools" not in meta:
            new_meta["allowed_tools"] = family
            changed = True

        if "review" not in meta and cap.evidence is not None:
            expected = cap.evidence(task.get("prompt") or "")
            if expected is not None:
                new_meta["review"] = {
                    "reviewer_agent_id": None, "criteria": "",
                    "evidence": [{"kind": expected.kind, "target": expected.target,
                                 "expect": dict(expected.expect)}],
                    "max_review_retries": 2,
                }
                changed = True

        if not changed:
            return None
        router_meta = dict(new_meta.get("action_contract") or {})
        router_meta.update({"capability": cap.name, "tools": family})
        new_meta["action_contract"] = router_meta
        await _set_meta(svc, task["id"], new_meta)
        task["meta"] = new_meta
        await svc.bus.emit("action_contract.capability_selected", task_id=task["id"],
                           capability=cap.name, tools=family)
        return None
    return before_run


# ----------------------------------------------------------------- gate_completion

async def _gate(svc):
    async def gate_completion(task, run_id, answer):
        cap = classify(task.get("prompt") or "")
        if cap is None:
            return _verdict("NOT_APPLICABLE")
        if await _has_family_tool_call(svc, run_id, cap.tool_sources):
            # Реальная попытка инструментального пути семейства этой
            # capability — тексту ответа здесь сказать нечего (та же
            # качественная граница, что action_gate.py для browser).
            return _verdict("NOT_APPLICABLE")

        family_exists = bool(_family_tool_names(cap.tool_sources))
        if not family_exists:
            # Исполнителя для этой capability в реестре нет вообще (images/
            # workflow/schedules на момент этого патча) — честный терминал
            # сразу, без бессмысленного повтора одного и того же текста.
            await svc.bus.emit("action_contract.capability_unavailable",
                               task_id=task["id"], run_id=run_id, capability=cap.name)
            return _verdict("FAIL", rule=RULE, requeue=False, status="failed",
                           feedback=f"инструмент для {cap.name} недоступен в этой системе")

        has_tools = await _agent_has_family_tools(svc, task, cap.tool_sources)
        attempts, meta = await _attempts(svc, task["id"])
        if has_tools and attempts == 0:
            await _bump_attempts(svc, task["id"], meta)
            names = ", ".join(_family_tool_names(cap.tool_sources))
            return _verdict(
                "FAIL", rule=RULE, requeue=True,
                feedback=(f"Ответ описывает, что сделать, вместо того чтобы выполнить действие "
                         f"инструментом. Доступны инструменты: {names} — вызови нужный штатным "
                         f"механизмом, а не пересказывай шаги владельцу. Текстовая инструкция "
                         f"не завершает задачу."))

        await svc.bus.emit("action_contract.blocked", task_id=task["id"], run_id=run_id,
                           capability=cap.name, has_tools=has_tools)
        return _verdict("FAIL", rule=RULE, requeue=False, status="failed")
    return gate_completion


async def _setup(svc):
    svc.engine.add_hook("before_run", await _before_run(svc))
    svc.engine.add_hook("gate_completion", await _gate(svc))


FEATURE = Feature(name="action_contract", setup=_setup)
