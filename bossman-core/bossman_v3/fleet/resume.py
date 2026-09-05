"""FleetResumeKernel (§15): распределённое возобновление поверх журнала V3.

Второго хранилища чекпоинтов здесь нет намеренно (ZIP-овский
ExecutionCheckpoint/ResumePlanner заменён): истина о шагах — TaskJournal,
истина о мутациях — fleet_verified_mutations (идемпотентные ключи).

Решение после потери узла:
  * finished-шаги журнала остаются сделанными — не переигрываются;
  * следующий незакрытый шаг безопасен для переноса, если он READ_ONLY или
    IDEMPOTENT_WRITE, либо вообще не начинался (журнал PENDING без чека);
  * шаг REVERSIBLE/IRREVERSIBLE, начатый на потерянном узле и не подтверждённый —
    НЕ переносится автоматически: BLOCKED до решения владельца.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..contracts import SideEffectClass
from ..execution.compound import PlanStep
from ..memory.journal import FAILED, TaskJournal

SAFE_CLASSES = {SideEffectClass.READ_ONLY, SideEffectClass.IDEMPOTENT_WRITE}


@dataclass(frozen=True)
class ResumeDecision:
    resumable: bool
    next_step_id: str | None
    finished_steps: tuple[str, ...]
    reason: str


class FleetResumeKernel:
    def decide(self, journal: TaskJournal, plan: list[PlanStep], *, lost_in_flight: bool) -> ResumeDecision:
        finished = tuple(s.step_id for s in journal.finished())
        nxt = journal.next_step()
        if nxt is None:
            return ResumeDecision(True, None, finished, "all steps finished — nothing to resume")
        step = next((p for p in plan if p.step_id == nxt.step_id), None)
        if step is None:
            return ResumeDecision(False, nxt.step_id, finished, f"step {nxt.step_id!r} missing from plan")
        started = nxt.status == FAILED or bool(nxt.by) or bool(nxt.note)
        if lost_in_flight and started and step.action.side_effect not in SAFE_CLASSES:
            return ResumeDecision(False, nxt.step_id, finished,
                                  f"step {nxt.step_id!r} ({step.action.side_effect.value}) was in flight on a lost node "
                                  "and is not idempotent — owner decision required before re-execution")
        return ResumeDecision(True, nxt.step_id, finished,
                              "resume from first unfinished step" + (" (safe class)" if started else " (not started)"))
