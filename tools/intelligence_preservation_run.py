#!/usr/bin/env python3
"""Снять РЕАЛЬНОЕ парное измерение удержания интеллекта на локальной модели.

Гейт `tools/intelligence_preservation_gate.py` существовал без измерителя:
канонический `docs/benchmark/intelligence-preservation-current.json` никто не
производил, поэтому проверка Intelligence Preservation в CI падала не из-за
модели и не из-за кода, а из-за отсутствия файла. Это он и производит.

Что здесь измеряется: ОДНА модель на ОДНОМ наборе задач в четырёх полосах —

    RAW      только задача, без системного промпта;
    SYSTEM   + системный промпт оператора;
    CONTEXT  + срез контекста (память/факты, доступные модели);
    FULL     НАСТОЯЩАЯ петля выполнения Bossman.

AF-04 (аудит ASTRA_SKILLS_FREEZE_20260907): раньше полоса FULL дописывала к
промпту короткий СПИСОК ИМЁН инструментов и обращалась прямо в Ollama. Это
prompt-smoke: он не доказывает, что обвязка сохраняет качество модели, потому
что обвязки в нём нет. Теперь FULL — это не рендер строки, а исполнение:

  * системный промпт строит production-функция `bossman.runner._system_prompt`
    (строки инструментов из настоящего REGISTRY, memory.md в рамке provenance);
  * схемы инструментов даёт production-функция `bossman.runner._tool_schemas`
    (настоящие JSON-схемы `ToolDef.schema()`, а не имена в тексте);
  * сообщения собирает НАСТОЯЩИЙ механизм контекста `bossman.context.
    ContextBuilder` с бюджетом блоков от `bossman.llm.real_window`: тот же
    порядок блоков, та же рамка «данные ≠ инструкции» (RETRIEVED_DATA_HEADER),
    то же схлопывание истории и тот же порог уплотнения;
  * петля многошаговая: модель возвращает tool_calls -> инструмент РЕАЛЬНО
    исполняется через свой handler -> результат возвращается модели через
    `builder.add_tool_result` -> следующий шаг. Вызовы инструментов
    НАБЛЮДАЕМЫ: они попадают в трассу полосы и в payload.

Что FULL здесь НЕ делает (названо прямо, а не умолчано): нет Postgres/Redis,
нет очереди задач, нет approvals владельца и нет Telegram — их в измерении
нет по построению. Поэтому инструмент, которому нужно подтверждение, здесь
ОТКЛОНЯЕТСЯ (владельца, который нажал бы «да», в прогоне нет), а исполняются
только read-инструменты из `BENCH_EXECUTABLE_TOOLS` внутри песочницы. Это
записано в `lanes.full` каждого payload, чтобы никто не прочитал результат как
приёмку всей системы.

Свойство, которое обязано ломаться заметно: если полосу FULL когда-нибудь
снова сведут к дописыванию списка имён инструментов, `full_lane_violations`
вернёт нарушения, прогон откажется писать файл, а тесты
`tests/test_intelligence_preservation_runner.py` упадут.

Честность встроена, а не декларируется:

  * ничего не выдумывается. Модель вызывается по-настоящему; при отсутствии
    ответа задача считается проваленной, а не пропускается;
  * `evaluated_sha` берётся из git и обязан совпасть с проверяемым коммитом —
    вердикт по старому SHA не является вердиктом по новому;
  * если задач меньше порога, файл всё равно пишется, но гейт ответит
    INSUFFICIENT_EVIDENCE. Это правильный ответ, а не дефект;
  * оценка каждого пункта — детерминированная проверка (точное совпадение,
    разбор JSON, имя инструмента), а не мнение второй модели;
  * temperature=0 и seed фиксируют СЭМПЛЕР, а не среду. Драйвер, сборка
    сервера, квантизация, батчинг и KV-кэш в обещание не входят.
    Повторяемость доказывается повторным прогоном, а не ссылкой на seed.

Запуск на машине владельца (модель уже поднята локально):

    python tools/intelligence_preservation_run.py \\
        --model qwen2.5-coder:14b \\
        --endpoint http://127.0.0.1:11435 \\
        --out docs/benchmark/intelligence-preservation-current.json

Затем:

    python tools/intelligence_preservation_gate.py \\
        docs/benchmark/intelligence-preservation-current.json \\
        --expect-sha $(git rev-parse HEAD)
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
# production-код Bossman лежит в bossman-core; полоса FULL импортирует его,
# а не переписывает у себя.
_CORE = ROOT / "bossman-core"
if _CORE.is_dir() and str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

MODES = ("raw", "system", "context", "full")
BASELINE = "raw"

# Метрики, которых требует гейт. Каждая задача набора объявляет, какую из них
# она измеряет; метрика без задач честно получает 0 задач, и гейт это увидит.
REQUIRED_METRICS = (
    "reasoning_accuracy", "coding_correctness", "structured_output_accuracy",
    "unknown_task_adaptation", "tool_selection_accuracy", "schema_argument_accuracy",
    "long_context_accuracy", "memory_retrieval_accuracy",
    "computer_use_planning_accuracy", "task_completion_rate", "hallucination_rate",
)
# Для этой метрики единица — ПЛОХО (доля выдумок), гейт её так и трактует.
LOWER_IS_BETTER = ("hallucination_rate",)

# Порог `--min-samples 20` в CI — это НИЖНЯЯ ГРАНИЦА ДОПУСТИМОСТИ, а не
# достаточность. Утверждение «удержано не меньше 98%» — это доверительная
# нижняя граница, и она зависит от размера выборки. Измерено прямым прогоном
# гейта: даже БЕЗУПРЕЧНЫЙ парный прогон (ни одной потерянной задачи) получает
# PASS только начиная со 189 задач на метрику; при 20 задачах ответ будет
# INSUFFICIENT_EVIDENCE, каким бы идеальным ни был результат. Число названо
# здесь, чтобы владелец узнавал это ДО многочасового локального прогона, а не
# после него.
SUFFICIENT_SAMPLES_PER_METRIC = 189

SYSTEM_PROMPT = (
    "Ты — BOSSMAN, оператор компьютера владельца. Отвечай кратко и по существу. "
    "Если просят JSON — верни только JSON, без пояснений и без markdown-ограждений."
)

# Полоса FULL исполняет настоящих агентов Bossman. Агент по умолчанию —
# read-only analyst: его гранты (fs.read/fs.search/fs.list/search_journal/log)
# не меняют мир, а сам он объявлен в production, а не в бенчмарке.
FULL_LANE_AGENT = "analyst"
# Что полоса FULL действительно ИСПОЛНЯЕТ. Всё остальное отклоняется тем же
# текстом, которым production отказывает в неподтверждённом действии. Список
# узкий намеренно: измерение не имеет права трогать мир владельца.
BENCH_EXECUTABLE_TOOLS = ("fs.read", "fs.list", "fs.search", "search_journal")
FULL_LANE_MAX_STEPS = 6

# Коды нарушений контракта полосы FULL. Каждый — поведенческий: он получается
# прогоном полосы через пробную модель, а не сравнением исходника со строкой.
CONTRACT_NO_TOOL_SURFACE = "NO_STRUCTURED_TOOL_SURFACE"
CONTRACT_SINGLE_TURN = "SINGLE_TURN_ONLY"
CONTRACT_TOOL_NOT_EXECUTED = "TOOL_CALL_NOT_EXECUTED"
CONTRACT_RESULT_NOT_RETURNED = "TOOL_RESULT_NOT_RETURNED_TO_MODEL"
CONTRACT_CONTEXT_NOT_FRAMED = "CONTEXT_NOT_FRAMED_AS_DATA"
FULL_LANE_CONTRACT_CODES = (
    CONTRACT_NO_TOOL_SURFACE, CONTRACT_SINGLE_TURN, CONTRACT_TOOL_NOT_EXECUTED,
    CONTRACT_RESULT_NOT_RETURNED, CONTRACT_CONTEXT_NOT_FRAMED,
)


class RunnerError(RuntimeError):
    """Измерение не состоялось. Пустой результат — не результат."""


# --------------------------------------------------------------- модель
@dataclass(frozen=True)
class ToolCall:
    """Вызов инструмента так, как его вернула модель (api-имя: точки -> _)."""
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelReply:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


def normalize_reply(raw: Any) -> ModelReply:
    """Ответ модели в едином виде. Строка — это ответ без вызовов инструментов."""
    if isinstance(raw, ModelReply):
        return raw
    if raw is None:
        return ModelReply()
    if isinstance(raw, str):
        return ModelReply(content=raw)
    if isinstance(raw, Mapping):
        calls: list[ToolCall] = []
        for item in raw.get("tool_calls") or []:
            fn = item.get("function", item) if isinstance(item, Mapping) else {}
            name = str(fn.get("name") or "")
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args or "{}")
                except json.JSONDecodeError:
                    args = {}
            if not isinstance(args, Mapping):
                args = {}
            if name:
                calls.append(ToolCall(name, dict(args)))
        return ModelReply(content=str(raw.get("content") or ""), tool_calls=calls)
    raise RunnerError(f"model client returned {type(raw).__name__}, not a reply")


ModelCall = Callable[[list[dict[str, str]], "list[dict] | None"], ModelReply]


def as_model_client(call: Callable[..., Any]) -> ModelCall:
    """Принять и одноаргументный клиент (`call(messages)`), и клиент с
    инструментами (`call(messages, tools=...)`). Полосы RAW/SYSTEM/CONTEXT
    инструментов не предлагают, FULL — предлагает."""
    accepts_tools = False
    try:
        params = inspect.signature(call).parameters
    except (TypeError, ValueError):
        params = {}
    by_keyword = "tools" in params
    positional = [p for p in params.values()
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    accepts_tools = by_keyword or len(positional) >= 2 or any(
        p.kind is p.VAR_KEYWORD for p in params.values())

    def invoke(messages: list[dict[str, str]], tools: list[dict] | None = None) -> ModelReply:
        if accepts_tools and by_keyword:
            return normalize_reply(call(messages, tools=tools))
        if accepts_tools:
            return normalize_reply(call(messages, tools))
        return normalize_reply(call(messages))

    return invoke


# --------------------------------------------------------------- задачи
@dataclass(frozen=True)
class Task:
    task_id: str
    metric: str
    prompt: str
    expect: dict[str, Any]
    context: str = ""

    def rendered(self, mode: str) -> list[dict[str, str]]:
        """Промпт для ПРОМПТОВОЙ полосы (raw/system/context).

        FULL сюда не входит намеренно (AF-04): полосу выполнения нельзя
        получить дописыванием текста, поэтому её здесь просто нет — попытка
        отрендерить `full` строкой падает, а не тихо возвращает prompt-smoke.
        """
        if mode == "full":
            raise RunnerError(
                "the FULL lane is an execution lane, not a rendered prompt: "
                "use ProductionFullLane (bossman.runner + bossman.context), "
                "appending a list of tool names is the AF-04 regression")
        if mode not in MODES:
            raise RunnerError(f"unknown lane: {mode!r}")
        messages: list[dict[str, str]] = []
        if mode != "raw":
            messages.append({"role": "system", "content": SYSTEM_PROMPT})
        user = self.prompt
        if mode == "context" and self.context:
            user = f"Известное о проекте:\n{self.context}\n\nЗадача: {self.prompt}"
        messages.append({"role": "user", "content": user})
        return messages


@dataclass
class LaneRun:
    """Что полоса сделала на одном пункте. Трасса наблюдаема, а не подразумевается."""
    text: str = ""
    turns: int = 0
    requested_tools: list[str] = field(default_factory=list)   # что модель просила
    executed_tools: list[str] = field(default_factory=list)    # что РЕАЛЬНО исполнено
    declined_tools: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_trace(self) -> dict[str, Any]:
        out: dict[str, Any] = {"turns": self.turns}
        if self.requested_tools:
            out["requested_tools"] = list(self.requested_tools)
        if self.executed_tools:
            out["executed_tools"] = list(self.executed_tools)
        if self.declined_tools:
            out["declined_tools"] = list(self.declined_tools)
        if self.notes:
            out["notes"] = list(self.notes)
        return out


@dataclass
class LaneMetric:
    passed: float = 0.0
    samples: int = 0
    lost: int = 0
    gained: int = 0
    outcomes: dict[str, bool] = field(default_factory=dict)


class Lane(Protocol):
    mode: str

    def identity(self) -> dict[str, Any]: ...

    def run(self, task: Task, call: ModelCall) -> LaneRun: ...


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass
class PromptLane:
    """Полоса-абляция: та же задача, отличается только текстовая обвязка."""
    mode: str

    def identity(self) -> dict[str, Any]:
        return {
            "lane": self.mode,
            "kind": "prompt_ablation",
            "executes_tools": False,
            "system_prompt_sha256": _sha(SYSTEM_PROMPT) if self.mode != "raw" else None,
            "shows_context": self.mode == "context",
        }

    def run(self, task: Task, call: ModelCall) -> LaneRun:
        reply = call(task.rendered(self.mode), None)
        return LaneRun(text=reply.content, turns=1)


# ------------------------------------------------- настоящая полоса FULL
def _production():
    """Импорт production-модулей Bossman. Отсутствие ядра — это отказ измерения,
    а НЕ тихий откат к prompt-smoke: молча выдать smoke за FULL и есть AF-04."""
    try:
        from bossman import runner as bossman_runner
        from bossman.agents import load_agent
        from bossman.context import RETRIEVED_DATA_HEADER, ContextBudget, ContextBuilder
        from bossman.llm import real_window
        from bossman.toolkit import REGISTRY, ToolContext, by_api_name
    except Exception as exc:  # noqa: BLE001 — причина важнее типа
        raise RunnerError(
            "the FULL lane needs the production core (bossman-core) importable: "
            f"{type(exc).__name__}: {exc}. Without it there is no full lane to "
            "measure — INSUFFICIENT_EVIDENCE, not a prompt-only substitute") from exc
    return {
        "runner": bossman_runner, "load_agent": load_agent,
        "RETRIEVED_DATA_HEADER": RETRIEVED_DATA_HEADER,
        "ContextBudget": ContextBudget, "ContextBuilder": ContextBuilder,
        "real_window": real_window, "REGISTRY": REGISTRY,
        "ToolContext": ToolContext, "by_api_name": by_api_name,
    }


def _await(coro) -> Any:
    """Исполнить async-handler инструмента из синхронного измерителя."""
    return asyncio.run(coro)


class ProductionFullLane:
    """FULL = настоящая петля Bossman, а не строка с именами инструментов.

    Собирается из production-кода: `_system_prompt` и `_tool_schemas` из
    `bossman.runner`, `ContextBuilder`/`ContextBudget` из `bossman.context`,
    окно из `bossman.llm.real_window`, инструменты из `bossman.toolkit.REGISTRY`.
    Здесь не воспроизводится ни один из этих кусков — они вызываются.
    """

    mode = "full"

    def __init__(self, *, agent_name: str = FULL_LANE_AGENT, workdir: Path | None = None,
                 max_steps: int = FULL_LANE_MAX_STEPS, context_engine: bool = False,
                 agents_dir: Path | None = None):
        p = _production()
        self._p = p
        self.agents_dir = agents_dir or (ROOT / "bossman-core" / "agents")
        agent_path = self.agents_dir / agent_name
        if not (agent_path / "agent.yaml").exists():
            raise RunnerError(f"no production agent at {agent_path}: the FULL lane "
                              "runs a real agent, not an invented one")
        self.agent = p["load_agent"](agent_path)
        self.max_steps = max(2, int(max_steps))
        self.context_engine = bool(context_engine)
        self._workdir_is_temp = workdir is None
        self.workdir = Path(workdir) if workdir else Path(
            tempfile.mkdtemp(prefix="bossman-intel-full-"))
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.journal = self.workdir / "journal.md"
        if not self.journal.exists():
            self.journal.write_text("# journal (intelligence benchmark sandbox)\n",
                                    encoding="utf-8")
        # production-функции, а не их пересказ
        self.system_prompt = p["runner"]._system_prompt(self.agent)
        self.tool_schemas = p["runner"]._tool_schemas(self.agent)
        self.window = int(p["real_window"](self.agent.model))
        self._external_header = getattr(p["runner"], "EXTERNAL_DATA_HEADER", "")
        self._internal_safe = getattr(p["runner"], "INTERNAL_SAFE_TOOLS", frozenset())
        self.stats: dict[str, int] = {}
        self.reset_stats()

    def reset_stats(self) -> None:
        """Обнулить наблюдаемые счётчики. Вызывается после самопроверки, чтобы
        числа в payload описывали ИЗМЕРЕНИЕ, а не пробу контракта."""
        self.stats = {"model_turns": 0, "executed": 0, "declined": 0,
                      "items_with_executed_tool_call": 0}

    # -- наблюдаемость --------------------------------------------------
    def sandbox_root(self) -> Path:
        return self.workdir

    def _registry_fingerprint(self) -> str:
        reg = self._p["REGISTRY"]
        return _sha("\n".join(f"{n}|{t.rights}|{sorted(t.params)}"
                              for n, t in sorted(reg.items())))

    def _agent_fingerprint(self) -> str:
        parts = []
        for name in ("agent.yaml", "prompt.md", "memory.md"):
            f = (self.agent.path or Path(".")) / name
            parts.append(f.read_text(encoding="utf-8") if f.exists() else "")
        return _sha("\x00".join(parts))

    def identity(self) -> dict[str, Any]:
        budget = self._p["ContextBudget"](window=self.window)
        return {
            "lane": "full",
            "kind": "production_execution_loop",
            "executes_tools": True,
            "agent": self.agent.name,
            "agent_model_alias": self.agent.model,
            "agent_sha256": self._agent_fingerprint(),
            "system_prompt_sha256": _sha(self.system_prompt),
            "system_prompt_builder": "bossman.runner._system_prompt",
            "tool_schema_builder": "bossman.runner._tool_schemas",
            "tool_schema_count": len(self.tool_schemas),
            "tool_registry_sha256": self._registry_fingerprint(),
            "tool_registry_size": len(self._p["REGISTRY"]),
            "context_mechanism": "bossman.context.ContextBuilder",
            "context_window": self.window,
            "context_block_limits": dict(budget.limits),
            "context_engine": "on" if self.context_engine else "off",
            "executable_tools": list(BENCH_EXECUTABLE_TOOLS),
            "max_steps": self.max_steps,
            "sandbox_workdir": str(self.workdir),
            "not_covered": [
                "postgres/redis task queue", "owner approvals (waiting_approval)",
                "telegram notifications", "write/exec/send tools",
            ],
        }

    # -- исполнение -----------------------------------------------------
    def _dispatch(self, tool_call: ToolCall, ctx) -> tuple[str, str, str, str]:
        """(rendered, one_line, status, canonical_name) — форма `runner._call_tool`
        без Postgres и без approvals, которых в измерении нет."""
        by_api_name = self._p["by_api_name"]
        tool = by_api_name(tool_call.name)
        if tool is None:
            return (f"нет такого инструмента: {tool_call.name}",
                    f"{tool_call.name}: нет инструмента", "unknown", tool_call.name)
        grant = self.agent.grant(tool.name)
        if grant is None:
            return (f"инструмент {tool.name} не выдан агенту {self.agent.name} — отказ",
                    f"{tool.name}: отказ (не выдан)", "not_granted", tool.name)
        needs_confirm = grant.confirm if grant.confirm is not None else tool.confirm_default
        if tool.mandatory_confirm is not None:
            try:
                needs_confirm = needs_confirm or bool(tool.mandatory_confirm())
            except Exception:  # noqa: BLE001 — сбой предиката = «нужно спросить»
                needs_confirm = True
        if needs_confirm:
            # В измерении нет владельца, который нажал бы «да». Production в этом
            # случае отвечает моделью тем же отказом, а не выполняет действие.
            return (f"действие {tool.name} отклонено пользователем — не выполнять и не повторять",
                    f"{tool.name}: отклонено", "needs_confirm", tool.name)
        if tool.name not in BENCH_EXECUTABLE_TOOLS:
            return (f"инструмент {tool.name} недоступен в измерении (только {', '.join(BENCH_EXECUTABLE_TOOLS)}) — отказ",
                    f"{tool.name}: недоступен в измерении", "not_executable", tool.name)
        try:
            result = _await(tool.handler(dict(tool_call.arguments), ctx))
        except Exception as exc:  # ошибка инструмента — данные для модели
            return (f"ошибка {tool.name}: {exc}", f"{tool.name}: ошибка", "error", tool.name)
        rendered = result.render()
        if tool.name not in self._internal_safe:
            rendered = self._external_header + rendered
        return (rendered, result.one_line or f"{tool.name}: выполнено", "executed", tool.name)

    def run(self, task: Task, call: ModelCall) -> LaneRun:
        p = self._p
        builder = p["ContextBuilder"](p["ContextBudget"](window=self.window),
                                      self.system_prompt)
        tools = list(self.tool_schemas)
        if self.context_engine:
            tools = p["runner"].apply_context_engine(
                builder, tools, project=self.agent.name, task_text=task.prompt,
                memory_md=self.agent.memory)
        if task.context:
            # Настоящий механизм: контекст входит блоком `retrieved` с provenance
            # и рамкой «это ДАННЫЕ», а не склейкой в текст задачи.
            builder.set_retrieved([
                f"источник: docs/benchmark/intelligence_tasks.json#{task.task_id}\n"
                f"{task.context}"])
        ctx = p["ToolContext"](agent=self.agent.name, run_id=None, workdir=self.workdir,
                               journal=self.journal, notes_dir=self.workdir)
        out = LaneRun()
        report = builder.pruning_report()
        if report:
            out.notes.append(f"context budget pruned: {sorted(report)}")
        executed_here = False
        while out.turns < self.max_steps:
            if builder.needs_compaction(task.prompt):
                summary = call(builder.compaction_messages(), None)
                out.turns += 1
                self.stats["model_turns"] += 1
                builder.apply_compaction(summary.content)
                out.notes.append("compacted")
            out.turns += 1
            self.stats["model_turns"] += 1
            reply = call(builder.build(task.prompt), tools or None)
            if not reply.tool_calls:
                out.text = reply.content
                break
            builder.add_assistant(reply.content or
                                  "; ".join(c.name for c in reply.tool_calls))
            for tool_call in reply.tool_calls:
                out.requested_tools.append(tool_call.name)
                rendered, one_line, status, canonical = self._dispatch(tool_call, ctx)
                if status == "executed":
                    out.executed_tools.append(canonical)
                    self.stats["executed"] += 1
                    executed_here = True
                else:
                    out.declined_tools.append(f"{canonical}:{status}")
                    self.stats["declined"] += 1
                builder.add_tool_result(canonical, rendered, one_line)
        else:
            out.notes.append("max_steps reached")
        if executed_here:
            self.stats["items_with_executed_tool_call"] += 1
        return out


def build_lanes(*, full: Lane | None = None, **full_kwargs: Any) -> dict[str, Lane]:
    lanes: dict[str, Lane] = {m: PromptLane(m) for m in MODES if m != "full"}
    lanes["full"] = full if full is not None else ProductionFullLane(**full_kwargs)
    return lanes


# ------------------------------------- контракт полосы FULL (поведенческий)
def full_lane_violations(lane: Any) -> list[str]:
    """Прогнать полосу через пробную модель и вернуть коды нарушений.

    Это не сверка исходника со строкой: полосе подсовывается модель, которая на
    первом шаге ПРОСИТ вызов инструмента, и проверяется, что полоса
    (1) предложила модели настоящие СХЕМЫ инструментов, (2) сходила к модели
    больше одного раза, (3) действительно исполнила инструмент, (4) вернула его
    вывод модели и (5) внесла контекст отдельным блоком «данные», а не склейкой
    в текст задачи. Полоса, которая просто дописывает список имён инструментов,
    проваливает все пять — что и требуется (AF-04).

    Пять проверок не зависят от того, установлено ли ядро: без bossman-core они
    выполняются в структурной форме (схема с типами, отдельный блок контекста),
    а с ядром дополнительно требуют, чтобы каждое имя резолвилось в настоящий
    `ToolDef` и чтобы блок нёс production-рамку RETRIEVED_DATA_HEADER. Так
    негативный контроль AF-04 работает и в тонком CI-окружении.
    """
    try:
        p = _production()
    except RunnerError:
        p = None
    by_api_name = p["by_api_name"] if p else None
    retrieved_header = p["RETRIEVED_DATA_HEADER"] if p else None
    nonce_file = "intelligence_probe.txt"
    nonce = "PROBE-FILE-" + hashlib.sha256(b"intelligence-full-lane-probe").hexdigest()[:12]
    context_nonce = "PROBE-CONTEXT-" + hashlib.sha256(b"probe-context").hexdigest()[:12]
    root = None
    if hasattr(lane, "sandbox_root"):
        try:
            root = Path(lane.sandbox_root())
            root.mkdir(parents=True, exist_ok=True)
            (root / nonce_file).write_text(nonce + "\n", encoding="utf-8")
        except OSError:
            root = None

    seen: list[dict[str, Any]] = []

    def probe(messages, tools=None):
        seen.append({"messages": [dict(m) for m in messages], "tools": tools})
        if len(seen) == 1:
            return ModelReply(content="", tool_calls=[ToolCall("fs_read", {"path": nonce_file})])
        return ModelReply(content="готово")

    task = Task(task_id="__full_lane_probe__", metric="tool_selection_accuracy",
                prompt="ПРОБА полосы FULL: прочитай файл и подтверди.",
                expect={"kind": "contains", "all_of": [nonce]},
                context=f"{context_nonce}: пробный факт контекста.")
    result = lane.run(task, as_model_client(probe))

    violations: list[str] = []
    first = seen[0] if seen else {"messages": [], "tools": None}
    offered = first.get("tools") or []
    structured = []
    for entry in offered:
        if not isinstance(entry, Mapping):
            continue
        fn = entry.get("function")
        if not isinstance(fn, Mapping) or not str(fn.get("name") or ""):
            continue
        params = fn.get("parameters")
        props = params.get("properties") if isinstance(params, Mapping) else None
        if not isinstance(props, Mapping):
            continue
        if by_api_name is not None and by_api_name(str(fn["name"])) is None:
            continue          # имя, которого нет в production REGISTRY, — не инструмент
        structured.append(props)
    if not structured or not any(structured):
        violations.append(CONTRACT_NO_TOOL_SURFACE)
    if len(seen) < 2 or getattr(result, "turns", 1) < 2:
        violations.append(CONTRACT_SINGLE_TURN)
    if "fs.read" not in list(getattr(result, "executed_tools", []) or []):
        violations.append(CONTRACT_TOOL_NOT_EXECUTED)
    later = "\n".join(m.get("content", "") for turn in seen[1:] for m in turn["messages"])
    if nonce not in later:
        violations.append(CONTRACT_RESULT_NOT_RETURNED)
    messages = first["messages"]
    task_message = messages[-1].get("content", "") if messages else ""
    carriers = [m for m in messages[:-1] if context_nonce in m.get("content", "")]
    if retrieved_header is not None:
        carriers = [m for m in carriers if retrieved_header in m.get("content", "")]
    if task_message.strip() != task.prompt.strip() or not carriers:
        # контекст склеен в текст задачи (или его вовсе нет отдельным блоком)
        violations.append(CONTRACT_CONTEXT_NOT_FRAMED)
    if root is not None:
        try:
            (root / nonce_file).unlink()
        except OSError:
            pass
    return violations


def assert_full_lane(lane: Any) -> None:
    """Отказаться измерять, если полоса FULL перестала быть полосой выполнения."""
    bad = full_lane_violations(lane)
    if bad:
        raise RunnerError(
            "the FULL lane does not execute the production loop: " + ", ".join(bad) +
            ". A lane that only appends a list of tool names is a prompt smoke "
            "(audit finding AF-04), not full-system retention")


# --------------------------------------------------------------- оценка
def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _first_json(text: str) -> Any:
    """Достать первый JSON-объект из ответа, не притворяясь, что его нет."""
    raw = str(text or "")
    fence = re.search(r"```(?:json)?\s*(.+?)```", raw, re.S)
    if fence:
        raw = fence.group(1)
    start = raw.find("{")
    if start < 0:
        raise ValueError("no JSON object in the answer")
    depth, quote = 0, ""
    for i in range(start, len(raw)):
        ch = raw[i]
        if quote:
            if ch == quote and raw[i - 1] != "\\":
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(raw[start:i + 1])
    raise ValueError("unterminated JSON object")


def score(task: Task, answer: str, run: LaneRun | None = None) -> bool:
    """Детерминированная проверка одного ответа. Никакой модели-судьи.

    Мнение второй модели — не измерение: оно само зависит от обвязки, которую
    мы и проверяем.

    Для `kind == "tool"` полоса, которая УМЕЕТ вызывать инструменты, засчитывает
    ещё и наблюдаемые вызовы: назвать инструмент и вызвать его — одно и то же
    умение, и наказывать полосу FULL за то, что она сделала дело вместо того,
    чтобы про него рассказать, было бы артефактом измерения, а не находкой.
    Соседний инструмент в вызовах так же считается ошибкой, как и в тексте.
    """
    expect = task.expect
    kind = expect.get("kind")
    text = str(answer or "")
    if kind == "equals":
        return _norm(text) == _norm(expect["value"])
    if kind == "contains":
        return all(_norm(v) in _norm(text) for v in expect["all_of"])
    if kind == "absent":
        # Проверка выдумок: правильный ответ — признать незнание, а не назвать
        # правдоподобное имя. Совпадение с приманкой считается провалом.
        return not any(_norm(v) in _norm(text) for v in expect["none_of"])
    if kind == "json_fields":
        try:
            obj = _first_json(text)
        except (ValueError, json.JSONDecodeError):
            return False
        if not isinstance(obj, dict):
            return False
        for key, value in expect["fields"].items():
            if key not in obj:
                return False
            if value is not None and _norm(obj[key]) != _norm(value):
                return False
        return True
    if kind == "tool":
        # Инструмент назван (или вызван) верно, если он есть, а соседей нет.
        observed = list(run.requested_tools) if run and run.requested_tools else []
        haystack = " ".join([text] + [n.replace("_", ".") for n in observed])
        wanted = _norm(expect["name"])
        if wanted not in _norm(haystack):
            return False
        return not any(_norm(other) in _norm(haystack) for other in expect.get("not", []))
    raise RunnerError(f"unknown expectation kind: {kind!r}")


# --------------------------------------------------------------- клиент
def ollama_client(endpoint: str, model: str, *, timeout: float = 120.0,
                  temperature: float = 0.0) -> ModelCall:
    """Клиент к локальной модели.

    temperature=0 и seed фиксируют сэмплер. Средой они не управляют: сборка
    сервера, драйвер, квантизация и батчинг могут менять результат, и seed
    этого не обещает. Повторяемость доказывается повторным прогоном.
    """
    url = endpoint.rstrip("/") + "/api/chat"

    def call(messages: list[dict[str, str]], tools: list[dict] | None = None) -> ModelReply:
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": False,
                                   "options": {"temperature": temperature, "seed": 7}}
        if tools:
            payload["tools"] = tools
        body = json.dumps(payload).encode()
        request = urllib.request.Request(url, data=body,
                                         headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise RunnerError(f"local model call failed: {type(exc).__name__}: {exc}") from exc
        return normalize_reply(data.get("message") or {})

    return call


def load_tasks(path: Path) -> list[Task]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    tasks = [Task(task_id=t["task_id"], metric=t["metric"], prompt=t["prompt"],
                  expect=t["expect"], context=t.get("context", "")) for t in raw["tasks"]]
    seen = {t.task_id for t in tasks}
    if len(seen) != len(tasks):
        raise RunnerError("duplicate task_id in the task set")
    unknown = sorted({t.metric for t in tasks} - set(REQUIRED_METRICS))
    if unknown:
        raise RunnerError(f"task set names metrics the gate does not know: {unknown}")
    return tasks


def head_sha() -> str:
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                         text=True, check=False)
    sha = (out.stdout or "").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RunnerError("cannot resolve HEAD: retention is measured on a commit")
    return sha


def measure(tasks: list[Task], call: Callable[..., Any], *, lanes: Mapping[str, Lane] | None = None,
            on_progress: Callable[[str, Task, bool], None] | None = None,
            traces: dict[str, dict[str, dict]] | None = None,
            ) -> dict[str, dict[str, LaneMetric]]:
    """Прогнать каждую задачу во ВСЕХ полосах и посчитать парное расхождение."""
    client = as_model_client(call)
    lane_impl: Mapping[str, Lane] = lanes if lanes is not None else {
        m: PromptLane(m) for m in MODES}
    missing = [m for m in MODES if m not in lane_impl]
    if missing:
        raise RunnerError(f"no lane implementation for {missing}")
    out: dict[str, dict[str, LaneMetric]] = {m: {} for m in MODES}
    for mode in MODES:
        lane = lane_impl[mode]
        for task in tasks:
            metric = out[mode].setdefault(task.metric, LaneMetric())
            try:
                run = lane.run(task, client)
                ok = score(task, run.text, run)
            except RunnerError:
                raise
            except Exception:
                # Модель не ответила или ответила негодным — это провал задачи,
                # а не повод её выбросить: выброшенная задача ломает парность.
                run, ok = LaneRun(notes=["lane raised"]), False
            metric.samples += 1
            metric.passed += 1.0 if ok else 0.0
            metric.outcomes[task.task_id] = ok
            if traces is not None:
                traces.setdefault(mode, {})[task.task_id] = run.as_trace()
            if on_progress is not None:
                on_progress(mode, task, ok)
    for mode in MODES:
        if mode == BASELINE:
            continue
        for name, metric in out[mode].items():
            base = out[BASELINE].get(name)
            if base is None:
                continue
            for task_id, ok in metric.outcomes.items():
                was = base.outcomes.get(task_id)
                if was is True and ok is False:
                    metric.lost += 1
                elif was is False and ok is True:
                    metric.gained += 1
    return out


def paired_items(lanes: dict[str, dict[str, LaneMetric]]) -> list[dict[str, Any]]:
    """Поэлементные ПАРНЫЕ результаты: один пункт — одна строка по всем полосам.
    Без них `lost`/`gained` нечем перепроверить."""
    rows: dict[str, dict[str, Any]] = {}
    for mode in MODES:
        for metric_name, metric in lanes[mode].items():
            for task_id, ok in metric.outcomes.items():
                row = rows.setdefault(task_id, {"task_id": task_id, "metric": metric_name})
                row[mode] = bool(ok)
    return [rows[k] for k in sorted(rows)]


def build_payload(lanes: dict[str, dict[str, LaneMetric]], *, model: str, dataset_id: str,
                  evaluated_sha: str, extra: dict[str, Any] | None = None,
                  lane_identity: Mapping[str, Any] | None = None,
                  traces: Mapping[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"model": model, "dataset_id": dataset_id,
                               "evaluated_sha": evaluated_sha, "modes": {}}
    payload.update(extra or {})
    for mode in MODES:
        block: dict[str, Any] = {}
        for name in REQUIRED_METRICS:
            metric = lanes[mode].get(name, LaneMetric())
            if metric.samples == 0:
                # Метрика без задач — это дефект НАБОРА, а не результат модели.
                # Написать сюда ноль значило бы отдать гейту payload, который он
                # честно назовёт негодным («samples must be positive»), и
                # владелец пошёл бы чинить гейт вместо своего набора задач.
                raise RunnerError(
                    f"the task set covers no items for {name!r}, which the gate requires; "
                    "add tasks for it instead of publishing an unmeasured metric")
            rate = metric.passed / metric.samples
            if name in LOWER_IS_BETTER:
                rate = 1.0 - rate          # доля выдумок, а не доля успехов
            item: dict[str, Any] = {"score": round(rate, 6), "samples": metric.samples}
            if mode != BASELINE:
                item["paired"] = {"lost": metric.lost, "gained": metric.gained}
            block[name] = item
        payload["modes"][mode] = block
    if lane_identity:
        payload["lanes"] = dict(lane_identity)
    payload["items"] = paired_items(lanes)
    counts = {name: lanes[BASELINE][name].samples for name in REQUIRED_METRICS
              if name in lanes[BASELINE]}
    payload["sufficiency"] = {
        "samples_per_metric": counts,
        "smallest_metric_samples": min(counts.values()) if counts else 0,
        "measured_samples_for_a_pass_verdict": SUFFICIENT_SAMPLES_PER_METRIC,
        "note": ("a perfect paired run answers INSUFFICIENT_EVIDENCE below "
                 f"{SUFFICIENT_SAMPLES_PER_METRIC} items per metric; this is the gate's "
                 "confidence bound, not a defect"),
    }
    if traces:
        payload["traces"] = dict(traces)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", required=True, help="точный id локальной модели")
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434",
                        help="локальный сервер модели (Ollama)")
    parser.add_argument("--tasks", type=Path,
                        default=ROOT / "docs" / "benchmark" / "intelligence_tasks.json")
    parser.add_argument("--out", type=Path,
                        default=ROOT / "docs" / "benchmark" / "intelligence-preservation-current.json")
    parser.add_argument("--dataset-id", default=None,
                        help="по умолчанию берётся из набора задач")
    parser.add_argument("--sha", default=None, help="по умолчанию — текущий HEAD")
    parser.add_argument("--quantization", default="", help="фактическая квантизация модели")
    parser.add_argument("--hardware", default="", help="на чём измерено")
    parser.add_argument("--agent", default=FULL_LANE_AGENT,
                        help="production-агент, которого исполняет полоса FULL")
    parser.add_argument("--full-workdir", type=Path, default=None,
                        help="песочница инструментов полосы FULL (по умолчанию временная)")
    parser.add_argument("--full-max-steps", type=int, default=FULL_LANE_MAX_STEPS)
    parser.add_argument("--context-engine", action="store_true",
                        help="включить production context_engine (пишет в его БД)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    tasks = load_tasks(args.tasks)
    per_metric: dict[str, int] = {}
    for task in tasks:
        per_metric[task.metric] = per_metric.get(task.metric, 0) + 1
    smallest = min(per_metric.values()) if per_metric else 0
    if smallest < SUFFICIENT_SAMPLES_PER_METRIC:
        # Предупреждение ДО прогона, а не разочарование после.
        print(
            f"ВНИМАНИЕ: самая бедная метрика даёт {smallest} задач; для вердикта PASS "
            f"гейту нужно не меньше {SUFFICIENT_SAMPLES_PER_METRIC} на метрику даже при "
            "безупречном прогоне. Этот прогон измерит полосы честно, но ответом гейта "
            "будет INSUFFICIENT_EVIDENCE, а не PASS.", file=sys.stderr)
    dataset_id = args.dataset_id or json.loads(
        args.tasks.read_text(encoding="utf-8")).get("dataset_id") or args.tasks.stem
    sha = args.sha or head_sha()

    full = ProductionFullLane(agent_name=args.agent, workdir=args.full_workdir,
                              max_steps=args.full_max_steps,
                              context_engine=args.context_engine)
    # Самопроверка ДО многочасового прогона: полоса FULL обязана быть полосой
    # выполнения. Деградировавшую полосу измерять бессмысленно.
    assert_full_lane(full)
    full.reset_stats()          # счётчики описывают измерение, а не пробу
    lanes = build_lanes(full=full)
    call = ollama_client(args.endpoint, args.model)

    done = {"n": 0}
    total = len(tasks) * len(MODES)

    def progress(mode: str, task: Task, ok: bool) -> None:
        done["n"] += 1
        if not args.quiet:
            print(f"[{done['n']:>4}/{total}] {mode:<7} {task.task_id:<28} "
                  f"{'ok' if ok else 'FAIL'}", file=sys.stderr)

    traces: dict[str, dict[str, dict]] = {}
    measured = measure(tasks, call, lanes=lanes, on_progress=progress, traces=traces)
    extra = {"decoding": "greedy(temperature=0,seed=7)",
             "determinism_note": ("a fixed seed pins the sampler, not the environment "
                                  "(server build, driver, quantization, batching); "
                                  "repeatability must be shown by a repeat run")}
    if args.quantization:
        extra["quantization"] = args.quantization
    if args.hardware:
        extra["hardware"] = args.hardware
    identity = {mode: lanes[mode].identity() for mode in MODES}
    identity["full"]["observed"] = dict(full.stats)
    payload = build_payload(measured, model=args.model, dataset_id=dataset_id,
                            evaluated_sha=sha, extra=extra, lane_identity=identity,
                            traces={"full": traces.get("full", {})})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"written: {args.out}", file=sys.stderr)
    print(f"tasks={len(tasks)} per lane x {len(MODES)} lanes; smallest metric has "
          f"{smallest} items (PASS needs {SUFFICIENT_SAMPLES_PER_METRIC})", file=sys.stderr)
    print(f"full lane: agent={full.agent.name} tools={len(full.tool_schemas)} "
          f"model_turns={full.stats['model_turns']} executed_tool_calls={full.stats['executed']}",
          file=sys.stderr)
    if full.stats["executed"] == 0:
        # Петля и механизм контекста отработали, но модель ни разу не вызвала
        # инструмент. Такой прогон нельзя пересказывать как проверку вызова
        # инструментов — обычно это модель без tool-calling на этом endpoint.
        print("ВНИМАНИЕ: полоса FULL не наблюдала НИ ОДНОГО вызова инструмента "
              "(lanes.full.observed.executed=0). Петля и контекст измерены, вызов "
              "инструментов — нет. Проверьте, что модель поддерживает tools в "
              "/api/chat, и не выдавайте этот прогон за проверку инструментов.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
