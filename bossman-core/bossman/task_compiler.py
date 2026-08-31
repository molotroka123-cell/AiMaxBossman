"""V2.6 — Task Compiler V2 (модуль F, core-часть): EV-гейт декомпозиции +
типизированное представление скомпилированной задачи.

НЕ «все промпты в DAG»: компиляция происходит только когда ожидаемая ценность
декомпозиции положительна (EV_decomp > 0). «2+2» остаётся прямым вызовом.
Исполнение DAG живёт в существующей архитектуре (bcc/v2/task_graph + engine;
в core — линейные планы projects/dev_factory) — этот модуль их НЕ дублирует,
он даёт общий типизированный контракт и решение «декомпозировать ли вообще».
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .signals import DecisionSignals


@dataclass(frozen=True, slots=True)
class CompiledStep:
    step_id: str
    action: str                       # capability/инструмент, не свободный текст
    depends_on: tuple[str, ...] = ()
    verification: str = ""            # критерий проверки шага
    tools: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CompiledTask:
    goal: str
    constraints: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()
    steps: tuple[CompiledStep, ...] = ()
    completion_criteria: str = ""
    risk_class: str = "normal"        # normal | sensitive | irreversible

    def ordered(self) -> list[CompiledStep]:
        """Топологический порядок шагов (Kahn); ValueError при цикле/битой ссылке."""
        by_id = {s.step_id: s for s in self.steps}
        indeg = {sid: 0 for sid in by_id}
        for s in self.steps:
            for d in s.depends_on:
                if d not in by_id:
                    raise ValueError(f"step {s.step_id!r} depends on unknown {d!r}")
                indeg[s.step_id] += 1
        ready = [sid for sid, n in indeg.items() if n == 0]
        out: list[CompiledStep] = []
        while ready:
            sid = ready.pop(0)
            out.append(by_id[sid])
            for s in self.steps:
                if sid in s.depends_on:
                    indeg[s.step_id] -= 1
                    if indeg[s.step_id] == 0:
                        ready.append(s.step_id)
        if len(out) != len(self.steps):
            raise ValueError("cycle in compiled steps")
        return out


def ev_decomp(*, delta_p_success: float, value: float,
              orchestration_cost: float, extra_context_cost: float) -> float:
    """EV_decomp = ΔP_success·Value − OrchestrationCost − ExtraContextCost."""
    return delta_p_success * value - orchestration_cost - extra_context_cost


def should_decompose(signals: DecisionSignals, *,
                     value: float = 1.0,
                     orchestration_cost: float = 0.15,
                     extra_context_cost: float = 0.1) -> tuple[bool, float]:
    """Декомпозировать только при положительном EV. ΔP_success оцениваем
    консервативно из сложности: простой задаче декомпозиция не помогает
    (ΔP≈0), сложной — до +0.5."""
    delta_p = max(0.0, signals.task_complexity - 0.2)
    ev = ev_decomp(delta_p_success=delta_p, value=value,
                   orchestration_cost=orchestration_cost,
                   extra_context_cost=extra_context_cost)
    return ev > 0.0, ev
