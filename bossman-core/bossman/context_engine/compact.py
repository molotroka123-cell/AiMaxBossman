from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from .models import MemoryKind, MemoryRecord
from .utils import normalize_space, token_estimate, unique_preserve


@dataclass(slots=True)
class Message:
    role: str
    content: str
    name: str = ""
    message_id: str = ""


@dataclass(slots=True)
class CompactResult:
    text: str
    input_tokens: int
    output_tokens: int
    preserved_recent_messages: int
    memory_refs: list[str] = field(default_factory=list)
    quality_checks: dict[str, bool] = field(default_factory=dict)
    anchors: list[str] = field(default_factory=list)
    overflow: bool = False


class CompactMemoryPlugin(Protocol):
    name: str
    def retrieve(self, query: str, project: str, limit: int) -> list[MemoryRecord]: ...


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?。！？])\s+|\n+", normalize_space(text))
    return [p.strip() for p in parts if p.strip()]


def _keywords(text: str) -> set[str]:
    stop = {"this","that","with","from","have","will","как","что","это","для","или","при","уже","его","она","они","так"}
    return {w.lower() for w in re.findall(r"[\w\-./]{3,}", text) if w.lower() not in stop}


# --- Anchor extraction -------------------------------------------------------
# Критические якоря — жёсткие токены, которые обязаны пережить compaction:
# числа с единицами, версии, пути к файлам, commit SHA, имя ветки, статус
# последнего прогона тестов. Потеря любого — quality gate FAIL.

_RE_NUM_UNIT = re.compile(r"\b\d+(?:[.,]\d+)?\s?(?:GB|MB|KB|TB|GHz|MHz|kB|ms|%|k|K)\b")
_RE_VERSION = re.compile(r"\bv\d+(?:\.\d+){0,3}\b|\b\d+\.\d+(?:\.\d+){1,2}\b")
_RE_PATH = re.compile(r"\b[\w.-]+/[\w./-]+\.[A-Za-z0-9]{1,6}\b")
_RE_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
_RE_BRANCH = re.compile(r"(?:ветк[аеиу]|branch)\s+([\w./-]+)", re.I)
_RE_TEST_STATUS = re.compile(r"\b\d+\s+(?:passed|failed|error[s]?|xfailed|skipped)\b", re.I)


def extract_anchors(text: str) -> list[str]:
    found: list[str] = []
    for rx in (_RE_TEST_STATUS, _RE_NUM_UNIT, _RE_VERSION, _RE_PATH):
        found.extend(m.group(0).strip() for m in rx.finditer(text))
    for m in _RE_BRANCH.finditer(text):
        found.append(m.group(1).strip(" .,;:!?)"))
    for m in _RE_SHA.finditer(text):
        tok = m.group(0)
        # исключаем чистые числа, уже покрытые другими правилами
        if any(c.isalpha() for c in tok):
            found.append(tok)
    return unique_preserve(found)


# --- Sentence classification into structured continuation-state buckets -------

_MK_CONSTRAINT = re.compile(r"\b(?:must|never|do not|don't|required|security|обязательно|нельзя|"
                            r"не\s+должен|не\s+удал|запрещ|constraint)\b", re.I)
_MK_DECISION = re.compile(r"\b(?:decision|decided|we will|chose|choose|делаем|решили|решение|выбрали|используем)\b", re.I)
_MK_FAILURE = re.compile(r"\b(?:bug|error|failed|failure|broken|regression|баг|ошибк|не\s+сработ|не\s+работ|сломал|провал)\b", re.I)
_MK_TEST = re.compile(r"\b(?:\d+\s+(?:passed|failed)|tests?\s+(?:pass|fail|green|red)|прогон|тест[аоы]?\s+(?:зелён|passed|failed))\b", re.I)
_MK_NEXT = re.compile(r"\b(?:todo|next|later|then\b|надо|нужно|потом|следующ|дальше|осталось|подключить)\b", re.I)
_MK_OPEN = re.compile(r"(?:\?|открыт\w*\s+вопрос|unresolved|open question|нерешён|под вопрос)", re.I)
_MK_CORRECTION = re.compile(r"\b(?:actually|correction|instead|не\s+забудь|поправк|на самом деле|вместо|уточнени)\b", re.I)
_MK_MARKER = re.compile(r"\b(?:priority|require|fix|приоритет|исправ|запомни)\b", re.I)
_RE_TECHNICAL = re.compile(r"(?:[\w.-]+/[\w./-]+|\b[A-Za-z_][A-Za-z0-9_]{2,}\(\)|\b\d+(?:\.\d+)?(?:%|GB|MB|k|K)?\b)")


class CompactSkill:
    """Conversation compactor → structured continuation state с memory-plugin
    hydration и anchor-survival guarantee.

    Extractive-first by design: критические факты копируются, не пересказываются,
    что исключает семантический дрейф (потерю числа, отрицания, версии, решения).
    Свободный LLM-summarizer может стоять ПОСЛЕ этой стадии, но обязан пройти те
    же anchor/quality checks. Compact — не summarize: он строит состояние
    продолжения (objective, constraints, active files, decisions, versions,
    bugs/failed approaches, test status, open threads, next actions, важные
    сообщения verbatim), гарантирует выживание критических якорей и при слишком
    маленьком budget поднимает overflow вместо тихой потери смысла.
    """

    def __init__(self, memory_plugins: list[CompactMemoryPlugin] | None = None) -> None:
        self.memory_plugins = memory_plugins or []

    def compact(self, messages: list[Message], *, project: str = "", target_tokens: int = 6000,
                keep_recent: int = 8, query: str = "") -> CompactResult:
        if not messages:
            return CompactResult("", 0, 0, 0,
                                 quality_checks={"nonempty": True, "within_budget": True,
                                                 "recent_preserved": True,
                                                 "memory_provenance_preserved": True,
                                                 "objective_preserved": True,
                                                 "anchors_preserved": True})
        raw = "\n".join(f"{m.role}: {m.content}" for m in messages)
        input_tokens = token_estimate(raw)
        recent = messages[-keep_recent:] if keep_recent else []
        older = messages[:-keep_recent] if keep_recent else messages
        objective = query.strip() or next((m.content for m in reversed(messages) if m.role == "user"), "")
        keys = _keywords(objective)

        full_text = "\n".join(m.content for m in messages)
        anchors = extract_anchors(full_text)

        # Категоризованная извлечённая память из старой истории. Каждая категория —
        # отдельный список строк "[role] sentence", дедуп с сохранением порядка.
        constraints: list[str] = []
        decisions: list[str] = []
        failures: list[str] = []
        tests: list[str] = []
        nexts: list[str] = []
        opens: list[str] = []
        corrections: list[str] = []
        history: list[str] = []
        older_ids = {id(m) for m in older}
        # Специализированные якорные категории извлекаются из ВСЕЙ переписки
        # (constraint/решение/баг/тест/вопрос, где бы он ни прозвучал), а общая
        # низкосигнальная история — только из старой части (свежий хвост и так
        # идёт verbatim и не дублируется в history).
        for m in messages:
            is_older = id(m) in older_ids
            for s in _sentences(m.content):
                tag = f"[{m.role}] {s}"
                classified = False
                if _MK_TEST.search(s):
                    tests.append(tag); classified = True
                if _MK_CONSTRAINT.search(s):
                    constraints.append(tag); classified = True
                if _MK_DECISION.search(s):
                    decisions.append(tag); classified = True
                if _MK_FAILURE.search(s):
                    failures.append(tag); classified = True
                if _MK_OPEN.search(s):
                    opens.append(tag); classified = True
                if _MK_NEXT.search(s):
                    nexts.append(tag); classified = True
                if _MK_CORRECTION.search(s) and m.role == "user":
                    corrections.append(tag); classified = True
                if not is_older:
                    continue
                sw = _keywords(s)
                relevance = len(keys & sw) / max(1, len(keys)) if keys else 0
                if not classified and (_MK_MARKER.search(s) or _RE_TECHNICAL.search(s) or relevance >= .20):
                    history.append(tag)
        constraints = unique_preserve(constraints)
        decisions = unique_preserve(decisions)
        failures = unique_preserve(failures)
        tests = unique_preserve(tests)
        nexts = unique_preserve(nexts)
        opens = unique_preserve(opens)
        corrections = unique_preserve(corrections)
        history = unique_preserve(history)

        # Активные файлы/ветка — из якорей (пути + имя ветки).
        active_files = [a for a in anchors if _RE_PATH.fullmatch(a)]
        branches = [m.group(1).strip(" .,;:!?)") for m in _RE_BRANCH.finditer(full_text)]
        versions_numbers = [a for a in anchors if _RE_VERSION.fullmatch(a) or _RE_NUM_UNIT.fullmatch(a)]

        memories: list[MemoryRecord] = []
        for plugin in self.memory_plugins:
            try:
                memories.extend(plugin.retrieve(objective, project, 12))
            except Exception:
                continue
        seen_mem: set[str] = set(); dedup_mem: list[MemoryRecord] = []
        for m in memories:
            if m.memory_id not in seen_mem:
                seen_mem.add(m.memory_id); dedup_mem.append(m)
        memories = dedup_mem[:12]

        # Каждая секция — (name, body, trimmable). Критические секции никогда не
        # обрезаются: objective, critical anchors, constraints, active files,
        # test status, open threads, важные сообщения verbatim, durable memory,
        # recent transcript. Обрезаемое — только низкосигнальная история и
        # производные списки (decisions/versions/failures/next) с хвоста.
        def _bullets(items: list[str]) -> str:
            return "\n".join(f"- {x}" for x in items)

        sections: list[tuple[str, str, bool]] = []
        sections.append(("Active objective", objective or "(not explicitly stated)", False))
        if anchors:
            sections.append(("Critical anchors", ", ".join(anchors), False))
        if constraints:
            sections.append(("Constraints", _bullets(constraints), False))
        if active_files or branches:
            body = ""
            if active_files:
                body += "files: " + ", ".join(unique_preserve(active_files))
            if branches:
                body += ("\n" if body else "") + "branch: " + ", ".join(unique_preserve(branches))
            sections.append(("Active files & branch", body, False))
        if tests:
            sections.append(("Test status", _bullets(tests), False))
        if decisions:
            sections.append(("Decisions", _bullets(decisions), True))
        if versions_numbers:
            sections.append(("Versions & numbers", ", ".join(unique_preserve(versions_numbers)), True))
        if failures:
            sections.append(("Bugs & failed approaches", _bullets(failures), True))
        if opens:
            sections.append(("Open threads", _bullets(opens), False))
        if nexts:
            sections.append(("Next actions", _bullets(nexts), True))
        if corrections:
            sections.append(("Important messages (verbatim)", "\n\n".join(corrections), False))
        if history:
            sections.append(("Preserved high-signal history", _bullets(history), True))
        if memories:
            sections.append(("Retrieved durable memory",
                             "\n".join(f"- [{m.kind.value}/{m.status.value}/{m.memory_id}] {m.text}"
                                       for m in memories), False))
        if recent:
            sections.append(("Recent transcript (verbatim)",
                             "\n\n".join(f"[{m.role}] {m.content}" for m in recent), False))

        def _render(secs: list[tuple[str, str, bool]]) -> str:
            return "\n\n".join(["# COMPACT HANDOFF"] + [f"## {n}\n{b}" for n, b, _ in secs if b.strip()])

        text = _render(sections)

        # Бюджетная обрезка: свежий transcript verbatim и критические секции не
        # трогаются; извлечённый низкосигнальный материал урезается с хвоста
        # (сначала history, затем прочие trimmable). Критические якоря
        # переживают всегда — если бюджет мал, поднимаем overflow, но не режем.
        if token_estimate(text) > target_tokens:
            trimmable_idx = [i for i, s in enumerate(sections) if s[2]]
            for i in reversed(trimmable_idx):
                if token_estimate(text) <= target_tokens:
                    break
                name, body, _ = sections[i]
                bullets = body.split("\n")
                while bullets and token_estimate(text) > target_tokens:
                    bullets.pop()
                    sections[i] = (name, "\n".join(bullets), True)
                    text = _render(sections)
                if not "\n".join(sections[i][1]).strip():
                    sections[i] = (name, "", True)
                    text = _render(sections)

        out_tokens = token_estimate(text)
        # Обязательные якоря обязаны присутствовать в финальном тексте.
        missing = [a for a in anchors if a not in text]
        anchors_preserved = not missing
        within_budget = out_tokens <= target_tokens
        overflow = (not within_budget) or (not anchors_preserved)
        checks = {
            "nonempty": bool(text.strip()),
            "within_budget": within_budget,
            "recent_preserved": all(m.content in text for m in recent),
            "memory_provenance_preserved": all(m.memory_id in text for m in memories),
            "objective_preserved": (not objective) or objective in text,
            "anchors_preserved": anchors_preserved,
        }
        return CompactResult(text, input_tokens, out_tokens, len(recent),
                             [m.memory_id for m in memories], checks,
                             anchors=anchors, overflow=overflow)
