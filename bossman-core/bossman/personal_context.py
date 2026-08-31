"""V2.6 — Personal Context Router (модуль N): отбор критики из memory.md.

Проблема: `_system_prompt` инжектит memory.md целиком, при этом context_engine
УЖЕ индексирует тот же memory.md и подаёт релевантные чанки через блок
retrieved — то есть память дублируется и платит токены дважды. Здесь — чистый
детерминированный отбор: в system остаются только жёсткие ограничения
(критические строки), остальное продолжает приходить ранжированными чанками.

Принцип KeepRisk: критические ограничения не удаляются ради токенов НИКОГДА —
маркеры ниже сознательно жадные (лучше оставить лишнюю строку, чем потерять
запрет). Модуль pure: без БД, без сети, без настроек — решение о включении
принимает runner._memory_for_system (RAW fallback обязателен).
"""
from __future__ import annotations

import re

# Маркеры жёстких ограничений (RU + EN). Регистронезависимо; строка, начинающаяся
# с "!" или содержащая "⚠", тоже критична — так владелец помечает запреты вручную.
CRITICAL_MARKERS = re.compile(
    r"(?i)(?:всегда|никогда|нельзя|запрещ|обязательно|только через|ВАЖНО"
    r"|critical|never|always|must|do not)"
    r"|⚠"
)

# Note, добавляемая render_selected: где искать остальную память.
RETRIEVED_NOTE = "Остальная память доступна через retrieved-блок (context_engine)"

_HEADING = re.compile(r"^#{1,6}\s")


def select_memory(memory_md: str) -> tuple[str, dict]:
    """Отобрать из memory.md только критические строки (+ контекст заголовков).

    Возвращает (critical_block, stats). В блок попадают строки, совпавшие с
    CRITICAL_MARKERS (или начинающиеся с "!"), каждая — вместе с ближайшим
    предшествующим markdown-заголовком (дедуплицированно, в исходном порядке):
    без заголовка «НИКОГДА не отправляй X» теряет привязку к разделу.
    stats = {"total_lines", "kept_lines"} — для наблюдаемости отбора.
    Детерминированно: одинаковый вход -> одинаковый выход.
    """
    lines = memory_md.splitlines()
    kept: list[str] = []
    kept_set: set[int] = set()  # индексы уже добавленных строк (дедуп заголовков)
    last_heading: int | None = None
    kept_lines = 0
    for i, line in enumerate(lines):
        if _HEADING.match(line):
            last_heading = i
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("!") or CRITICAL_MARKERS.search(line):
            # сначала — контекст: ближайший заголовок над строкой, один раз
            if last_heading is not None and last_heading not in kept_set:
                kept.append(lines[last_heading])
                kept_set.add(last_heading)
            kept.append(line)
            kept_set.add(i)
            kept_lines += 1
    stats = {"total_lines": len(lines), "kept_lines": kept_lines}
    return "\n".join(kept), stats


def render_selected(critical_block: str) -> str:
    """Текст для system-блока: критические ограничения + указатель на retrieved.

    Пустой critical_block -> только note: память не «исчезает», модель знает,
    что остальное придёт ранжированными чанками через context_engine.
    """
    block = critical_block.strip()
    if not block:
        return RETRIEVED_NOTE
    return f"{block}\n\n{RETRIEVED_NOTE}"
