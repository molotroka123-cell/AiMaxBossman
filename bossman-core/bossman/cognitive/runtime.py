"""Единый runtime: память + контекст + reasoning + длинные задачи как одна система.

Память без фильтра отравляет контекст; плохой контекст ломает reasoning;
неправильный reasoning портит DAG; слабый journal уничтожает результат после
перезапуска. Поэтому wiring — один класс `CognitiveRuntime` с общим бюджетом,
телеметрией, restart() и dashboard-снапшотом.

Адаптеры к существующему коду (подключает аудит, НЕ обязательно сразу):
- Postgres: заменить CognitiveStore путями на обёртку `bossman.db` pool —
  формат строк memories10/journal совместим (см. INTEGRATION-GUIDE §3);
- Retrieval: `HybridRetriever.search` → S-компонента R-формулы (memory.score_memory);
- WorkingMemory: task working-state ↔ контекстная секция "Current working state"
  и ThoughtState.goal/constraints (методы export/import ниже);
- Fable: реальный вызов через gateway клиент, решение — reasoning.should_call_fable;
- Gateway budget: CostGovernor ↔ budget_spent/budget_total в Checkpoint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

from .context import (
    CompiledPrompt,
    ContextCompiler,
    ContextItem,
    CriticalFact,
    CriticalFactLedger,
    FallbackSignals,
    InjectionFirewall,
    Priority,
    should_use_raw,
)
from .memory import (
    DEFAULT_WEIGHTS,
    MemoryRecord10,
    MemoryStore,
    RetrievalWeights,
    ScoredMemory,
    Tier,
    WriteEvidence,
    score_memory,
)
from .reasoning import (
    ComplexitySignals,
    FableOptions,
    ModeThresholds,
    ReasoningController,
    ReasoningMode,
    StopSignals,
    ThoughtState,
    complexity_score,
    should_call_fable,
    should_stop,
)
from .storage import CognitiveStore
from .tasks import (
    Checkpointer,
    EnvSnapshot,
    JournalStep,
    ResumeRecovery,
    StepState,
    TaskJournal,
)
from .verify import TrialResult, record_metric_event, verified_success_rate


class FableClient(Protocol):
    def ask(self, prompt: str, *, budget: float) -> dict[str, Any]: ...


@dataclass
class RuntimeConfig:
    db_path: str = "data/cognitive.sqlite3"
    context_budget_tokens: int = 8000
    task_budget_total: float = 10.0
    weights: RetrievalWeights = DEFAULT_WEIGHTS
    thresholds: ModeThresholds = ModeThresholds()


class CognitiveRuntime:
    """Фасад для агента/оркестратора. Поток одной задачи::

        begin_task → think (mode) → recall (память) → compile (контекст)
        → act (journal transitions) → checkpoint → finish (verify event)
        ... restart() после падения процесса ...
    """

    def __init__(self, config: RuntimeConfig | None = None) -> None:
        self.config = config or RuntimeConfig()
        self.store = CognitiveStore(self.config.db_path)
        self.memory = MemoryStore(self.store, weights=self.config.weights)
        self.ledger = CriticalFactLedger(self.store)
        self.compiler = ContextCompiler(ledger=self.ledger,
                                        firewall=InjectionFirewall())
        self.reasoning = ReasoningController(self.store, self.config.thresholds)
        self.journal = TaskJournal(self.store)
        self.checkpointer = Checkpointer(self.store, self.journal)
        self.recovery = ResumeRecovery(self.store, self.journal, self.checkpointer)
        self._fable: FableClient | None = None

    # -- wiring -----------------------------------------------------------
    def attach_fable(self, client: FableClient) -> None:
        self._fable = client

    def close(self) -> None:
        self.store.close()

    def restart(self, new_db_path: str = "") -> "CognitiveRuntime":
        """Пересоздание runtime (эмуляция перезапуска процесса).

        Durable-данные не теряются: SQLite переживает restart; проверка —
        memory.count_verified() до/после (требование restart-метрики).
        """
        self.close()
        if new_db_path:
            self.config.db_path = new_db_path
        fresh = CognitiveRuntime(self.config)
        return fresh

    # -- task lifecycle ------------------------------------------------------
    def begin_task(self, task_id: str, goal: str, *, run_id: str = "",
                   constraints: Sequence[str] = ()) -> JournalStep:
        step = JournalStep(task_id=task_id, run_id=run_id, step_id="goal",
                           goal=goal, constraints_text="\n".join(constraints))
        return self.journal.add_step(step)

    def think(self, task_id: str, state: ThoughtState, signals: ComplexitySignals,
              *, run_id: str = "", irreversible: bool = False,
              security_sensitive: bool = False) -> dict[str, Any]:
        if self.reasoning.unsupported_certainty(state):
            record_metric_event(
                self.store, "unsupported_certainty",
                {"task_id": task_id, "confidence": state.confidence},
                task_id=task_id, run_id=run_id,
            )
        return self.reasoning.start_thought(
            task_id=task_id, run_id=run_id, state=state, signals=signals,
            irreversible=irreversible, security_sensitive=security_sensitive)

    def recall(self, query: str, *, owner_id: str, project_id: str = "",
               allowed_consumer: str = "", limit: int = 12, **kw: Any) -> list[ScoredMemory]:
        return self.memory.search(query, owner_id=owner_id, project_id=project_id,
                                  allowed_consumer=allowed_consumer, limit=limit, **kw)

    def compile(
        self,
        items: Sequence[ContextItem],
        *,
        fallback: FallbackSignals | None = None,
        raw_texts: Sequence[str] = (),
    ) -> dict[str, Any]:
        """Компиляция + raw fallback. При use_raw=True возвращается сырой текст
        вместо сжатого (требование §2 ТЗ)."""
        fb = should_use_raw(fallback) if fallback else {"use_raw": False, "reasons": []}
        if fb["use_raw"]:
            raw = "\n\n--- RAW ---\n\n".join(raw_texts) if raw_texts else ""
            return {"raw": True, "reasons": fb["reasons"], "text": raw,
                    "prompt": None}
        prompt = self.compiler.compile(items, budget_tokens=self.config.context_budget_tokens)
        return {"raw": False, "reasons": [], "text": prompt.render(), "prompt": prompt}

    def route_fable(self, prompt: str, opt: FableOptions, *,
                    local_ev: float = 0.0, p0_security: bool = False,
                    budget: float = 1.0) -> dict[str, Any]:
        decision = should_call_fable(opt, local_continuation_ev=local_ev,
                                     p0_security=p0_security)
        if decision["call"] and self._fable is not None:
            return {"called": True, "result": self._fable.ask(prompt, budget=budget),
                    **decision}
        return {"called": False, "result": None, **decision}

    def checkpoint(self, task_id: str, **kw: Any) -> Any:
        return self.checkpointer.write(task_id, **kw)

    def recover(self, task_id: str, env: EnvSnapshot, probe: Callable) -> dict[str, Any]:
        return self.recovery.recover(task_id, current_env=env, effect_probe=probe)

    # -- WorkingMemory interop (существующий bossman.working_memory) ------------
    @staticmethod
    def working_state_to_thought(wm_state: dict[str, Any]) -> ThoughtState:
        """Экспорт Postgres WorkingMemory → ThoughtState (адаптер, без импорта)."""
        return ThoughtState(
            goal=str(wm_state.get("objective", "")),
            constraints=list(wm_state.get("constraints", []) or []),
            verified_facts=list(wm_state.get("decisions", []) or []),
            unknowns=list(wm_state.get("open_questions", []) or []),
            next_action=str((wm_state.get("next_action", {}) or {}).get("action", "")
                            if isinstance(wm_state.get("next_action"), dict)
                            else wm_state.get("next_action", "")),
        )

    @staticmethod
    def thought_to_context_items(state: ThoughtState) -> list[ContextItem]:
        return [
            ContextItem("Current working state", state.goal, Priority.P1, "working-memory"),
            ContextItem("Critical constraints", "\n".join(state.constraints),
                        Priority.P1, "working-memory"),
            ContextItem("Unresolved questions", "\n".join(state.unknowns),
                        Priority.P3, "working-memory"),
        ]

    # -- observability ----------------------------------------------------------
    def dashboard(self) -> dict[str, Any]:
        cur = self.store.execute("SELECT COUNT(*) FROM memories10").fetchone()[0]
        tomb = self.store.execute("SELECT COUNT(*) FROM tombstones").fetchone()[0]
        open_cfg = self.store.execute(
            "SELECT COUNT(*) FROM conflicts WHERE resolution='open'").fetchone()[0]
        steps = self.store.execute(
            "SELECT state, COUNT(*) FROM journal GROUP BY state").fetchall()
        return {
            "memories": cur, "tombstones": tomb, "open_conflicts": open_cfg,
            "journal_by_state": {r[0]: r[1] for r in steps},
            "verified_memories": self.memory.count_verified(),
        }

    def finish_trial(self, trial: TrialResult) -> dict[str, Any]:
        record_metric_event(
            self.store, "trial",
            {"verified_success": trial.verified_success, "cost": trial.cost,
             "verifier": trial.verifier_id, "executor": trial.executor_id,
             **trial.metadata},
            task_id=trial.task_id, run_id=trial.run_id,
        )
        return {"recorded": True}
