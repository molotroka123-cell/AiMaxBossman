"""Работа с контекстом (раздел 10 ТЗ).

Контекст — оперативная память, а не диск: модель не помнит проект, она умеет его найти.
Раннер считает токены каждого блока перед вызовом; порядок блоков фиксирован
(неизменное впереди — ради KV-кэша llama.cpp), заполнение > 70 % → сначала уплотнение.
"""
from __future__ import annotations

from dataclasses import dataclass, field


def estimate_tokens(text: str) -> int:
    """Грубая оценка до вызова (~3 символа на токен для смеси русского и кода).
    Точные значения приходят из usage ответа и пишутся в model_calls."""
    return max(1, len(text) // 3)


# Доли блоков из 10.1 (лимиты на 64K; для другого окна — те же доли).
BLOCK_SHARES = {
    "system": 0.05,      # системный промпт + роль + список инструментов (одна строка на инструмент)
    "refs": 0.05,        # стабильные справочники: стиль-гайд, раскадровка, правила проекта
    "task": 0.015,       # спецификация текущей задачи
    "retrieved": 0.09,   # сводки этапов из notes/ + 3–5 чанков RAG
    "history": 0.25,     # рабочая история: последние вызовы инструментов, скользящее окно
}
RESERVE_SHARE = 0.30     # неприкосновенный запас
COMPACT_FILL = 0.70      # выше — сначала уплотнение, потом вызов
COLLAPSE_AFTER = 6       # результаты старше 6 вызовов схлопываются в одну строку
SUMMARY_MAX_TOKENS = 500

SUMMARY_FORM = """## Сводка {label}
Цель: …
Сделано: … (факты, пути к артефактам)
Решения: … (что и почему, одной строкой каждое)
Открыто: … (проблемы, вопросы к пользователю)
Дальше: … (следующий шаг, одна строка)"""

COMPACT_INSTRUCTION = (
    "Перепиши рабочую историю выше в сводку не длиннее 500 токенов, строго по форме:\n\n"
    + SUMMARY_FORM
    + "\n\nТолько факты и пути к файлам, без пересказа содержимого инструментов."
)


@dataclass
class HistoryItem:
    role: str            # assistant | tool
    content: str
    tool: str | None = None
    summary: str | None = None   # одна строка «<инструмент>: <итог>» для схлопывания


@dataclass
class ContextBudget:
    window: int                       # реальный потолок модели (10.7), не паспортный
    limits: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.limits:
            self.limits = {k: int(self.window * s) for k, s in BLOCK_SHARES.items()}

    @property
    def working_set(self) -> int:
        # рабочее множество: всё, кроме резерва на рассуждение и неприкосновенного запаса
        return int(self.window * (1 - RESERVE_SHARE))


class ContextBuilder:
    """Собирает сообщения для одного вызова модели и ведёт учёт токенов по блокам."""

    def __init__(self, budget: ContextBudget, system: str, refs: str = "",
                 key_constraint: str = ""):
        self.budget = budget
        self.system = self._fit(system, "system")
        self.refs = self._fit(refs, "refs")
        self.key_constraint = key_constraint
        self.retrieved: list[str] = []
        self.history: list[HistoryItem] = []
        self.summary: str | None = None   # результат последнего уплотнения

    # ---- наполнение ----

    def set_retrieved(self, chunks: list[str]) -> None:
        limit = self.budget.limits["retrieved"]
        out, used = [], 0
        for ch in chunks:
            t = estimate_tokens(ch)
            if used + t > limit:
                break
            out.append(ch)
            used += t
        self.retrieved = out

    def add_assistant(self, content: str) -> None:
        self.history.append(HistoryItem("assistant", content))

    def add_tool_result(self, tool: str, content: str, one_line: str) -> None:
        self.history.append(HistoryItem("tool", content, tool=tool, summary=one_line))

    # ---- обрезка и уплотнение ----

    def _fit(self, text: str, block: str) -> str:
        limit = self.budget.limits[block]
        if estimate_tokens(text) <= limit:
            return text
        return text[: limit * 3] + "\n[обрезано по бюджету блока]"

    def _history_messages(self) -> list[dict]:
        """Скользящее окно: старые результаты инструментов — одной строкой (10.4),
        свежие — целиком, пока блок history в лимите."""
        msgs: list[dict] = []
        n = len(self.history)
        used = 0
        limit = self.budget.limits["history"]
        for i, item in enumerate(self.history):
            is_old = n - i > COLLAPSE_AFTER
            if item.role == "tool":
                text = f"{item.tool}: {item.summary}" if is_old else item.content
                role = "user"
                text = f"[результат инструмента {item.tool}]\n{text}" if not is_old else text
            else:
                text, role = item.content, "assistant"
            t = estimate_tokens(text)
            if used + t > limit and not is_old:
                text = (item.summary and f"{item.tool}: {item.summary}") or text[: 200 * 3]
                t = estimate_tokens(text)
            used += t
            msgs.append({"role": role, "content": text})
        return msgs

    def fill(self, task_text: str) -> float:
        """Давление на окно считается по СЫРОЙ истории (до схлопывания):
        уже обрезанные блоки всегда в лимитах, а уплотнять нужно, когда
        накопленная история перестаёт помещаться в свой бюджет."""
        blocks = self.block_tokens(task_text)
        raw_history = sum(estimate_tokens(i.content) for i in self.history)
        total = blocks["system"] + blocks["refs"] + blocks["retrieved"] + blocks["task"] + raw_history
        return total / self.budget.window

    def needs_compaction(self, task_text: str) -> bool:
        return self.fill(task_text) > COMPACT_FILL

    def compaction_messages(self) -> list[dict]:
        """Запрос на уплотнение: модель переписывает историю в сводку ≤ 500 токенов."""
        msgs = [{"role": "system", "content": "Ты сжимаешь рабочую историю агента. Отвечай только сводкой."}]
        msgs += self._history_messages()
        msgs.append({"role": "user", "content": COMPACT_INSTRUCTION})
        return msgs

    def apply_compaction(self, summary: str) -> None:
        """Заменяет историю сводкой; полный лог остаётся в journal.md, не здесь.

        Инвариант против амнезии: историю НЕЛЬЗЯ стирать, не зафиксировав её в
        сводке.
        - Пустая сводка (резервный LLM вернул пусто / структурный compact не
          прошёл) = уплотнение НЕ удалось → историю сохраняем как есть. Раньше
          здесь безусловно шло `history = []`, и пустой ответ приводил к полной
          амнезии агента (P0).
        - Непустая сводка СЛИВАЕТСЯ с предыдущей, а не заменяет её: каждое
          следующее уплотнение видит только новые элементы истории (старые уже
          схлопнуты), поэтому простая замена постепенно забывала бы всё, что
          было сжато раньше. Результат урезаем с хвоста в бюджет — самое свежее
          (включая свежие якоря) выживает.
        """
        summary = (summary or "").strip()
        if not summary:
            return  # уплотнение не удалось — историю не трогаем, не будет амнезии
        merged = f"{self.summary}\n\n{summary}".strip() if self.summary else summary
        limit = SUMMARY_MAX_TOKENS * 3
        self.summary = merged[-limit:] if len(merged) > limit else merged
        self.history = []

    # ---- сборка вызова ----

    def block_tokens(self, task_text: str) -> dict[str, int]:
        return {
            "system": estimate_tokens(self.system),
            "refs": estimate_tokens(self.refs),
            "retrieved": estimate_tokens("\n".join(self.retrieved)) + (
                estimate_tokens(self.summary) if self.summary else 0),
            "history": sum(estimate_tokens(m["content"]) for m in self._history_messages()),
            "task": estimate_tokens(task_text) + estimate_tokens(self.key_constraint),
        }

    def build(self, task_text: str) -> list[dict]:
        """Порядок фиксирован (10.2): системный промпт → справочники → сводки →
        история → задача → одна строка ключевого ограничения (против потери середины)."""
        msgs: list[dict] = [{"role": "system", "content": self.system}]
        if self.refs:
            msgs.append({"role": "system", "content": self.refs})
        pulled = list(self.retrieved)
        if self.summary:
            pulled.insert(0, self.summary)
        if pulled:
            msgs.append({"role": "system", "content": "## Подтянутое из файлов\n" + "\n\n".join(pulled)})
        msgs += self._history_messages()
        task = task_text
        if self.key_constraint:
            task += f"\n\nКлючевое ограничение задачи: {self.key_constraint}"
        msgs.append({"role": "user", "content": task})
        return msgs
