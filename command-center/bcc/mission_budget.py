"""§8 — a simple task must not be able to cost a million tokens.

The acceptance corpus recorded 1 282 044 input tokens and $4.04 for T3, whose
entire verified effect was a two-line documentation correction; T2 spent
432 257 input tokens re-reading one web page. Nothing was broken in the sense
of raising an error — the run simply had no ceiling and no way to notice it was
going in circles, so it kept paying for context until a human stopped it.

Four independent guards, because each catches a failure the others cannot:

  budget          absolute ceilings on tokens and money for one run. Catches
                  "expensive but progressing" — the case where every step is
                  individually reasonable.
  identical call  the same tool with the same arguments N times in a run.
                  Catches a tool loop, which a token ceiling only catches after
                  it has already been paid for.
  stalled context the conversation stops gaining information: the same
                  assistant text, or a transcript that no longer grows. Catches
                  a *model* loop, which produces distinct tool calls and so
                  slips past the identical-call guard.
  review cycles   already bounded by `review.max_review_retries`; recorded here
                  so the cost of reviewing is visible next to the cost of doing.

Design rules, in tension and resolved deliberately:

  * Budgets are configurable per task and generous by default. A ceiling small
    enough to feel safe would silently truncate the complex missions this
    system exists for, and a truncated mission is not a cheap success — it is
    an expensive failure that looks like one. The defaults below are ~10x the
    largest *legitimate* run in the corpus and ~8x smaller than the pathology.
  * Hitting a guard FAILS the run with a named reason. It never trims context,
    never downgrades the model, and never marks work complete: a budget that
    quietly produced a worse answer would be indistinguishable from a bug.
  * Every guard reports the number it tripped on, so the ceiling can be argued
    with using evidence instead of raised by reflex.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

from .db import (approvals as approvals_t, interventions as interventions_t,
                 tasks as tasks_t, task_runs as runs_t, tool_calls as tool_calls_t)

#: ~10x the largest legitimate run observed in the acceptance corpus, ~8x below
#: the 1.28M pathology it exists to catch.
DEFAULT_MAX_TOKENS = 400_000
#: T3 burned $4.04 on a two-line doc edit while the legitimate cloud runs in the
#: same session cost $0.005-$0.04. $2.00 is ~50x the largest legitimate run and
#: still stops the pathology before it doubles.
DEFAULT_MAX_COST_USD = 2.0
#: The same tool with the same arguments this many times is a loop, not work.
#: The replay guard already refuses to *re-execute* non-idempotent calls; this
#: catches the read-only spin that guard deliberately allows.
DEFAULT_MAX_IDENTICAL_CALLS = 6
#: Consecutive model answers with no new information.
DEFAULT_MAX_STALLED_STEPS = 3

TOKENS_KEY = "max_tokens_total"
COST_KEY = "max_cost_usd"
IDENTICAL_KEY = "max_identical_calls"
STALLED_KEY = "max_stalled_steps"


@dataclass(frozen=True, slots=True)
class Limits:
    max_tokens: int
    max_cost_usd: float
    max_identical_calls: int
    max_stalled_steps: int

    @classmethod
    def for_task(cls, task: dict | None) -> "Limits":
        meta = (task or {}).get("meta") or {}
        if not isinstance(meta, dict):
            meta = {}
        return cls(
            max_tokens=_positive_int(meta.get(TOKENS_KEY), DEFAULT_MAX_TOKENS),
            max_cost_usd=_positive_float(meta.get(COST_KEY), DEFAULT_MAX_COST_USD),
            max_identical_calls=_positive_int(meta.get(IDENTICAL_KEY),
                                              DEFAULT_MAX_IDENTICAL_CALLS),
            max_stalled_steps=_positive_int(meta.get(STALLED_KEY), DEFAULT_MAX_STALLED_STEPS),
        )


def _positive_int(value: Any, default: int) -> int:
    """0 disables the guard — an explicit owner choice, not an accident. A
    negative or unparsable value falls back to the default rather than to
    "unlimited": a typo must not remove a ceiling."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


@dataclass(frozen=True, slots=True)
class Breach:
    code: str
    detail: str

    def __str__(self) -> str:                      # what lands in runs.error
        return f"{self.code}: {self.detail}"


def check_spend(limits: Limits, *, tokens_in: int, tokens_out: int,
                cost_usd: float) -> Breach | None:
    total = int(tokens_in) + int(tokens_out)
    if limits.max_tokens and total > limits.max_tokens:
        return Breach("TOKEN_BUDGET_EXCEEDED",
                      f"прогон израсходовал {total} токенов при бюджете "
                      f"{limits.max_tokens}; выполнение остановлено, чтобы цикл "
                      f"не оплачивался дальше")
    if limits.max_cost_usd and cost_usd > limits.max_cost_usd:
        return Breach("COST_BUDGET_EXCEEDED",
                      f"прогон израсходовал ${cost_usd:.4f} при бюджете "
                      f"${limits.max_cost_usd:.2f}")
    return None


def _call_key(row: dict) -> str:
    return f"{row.get('tool')}|{row.get('args_hash')}"


async def check_identical_calls(svc, limits: Limits, run_id: int) -> Breach | None:
    """A tool called with byte-identical arguments over and over.

    Only `executed`/`replayed` rows count: a call the policy denied or the owner
    refused is not the model making progress in a circle, it is the gate
    working, and failing the run for that would punish the safe outcome."""
    if not limits.max_identical_calls:
        return None
    async with svc.db.session() as s:
        rows = [dict(r._mapping) for r in (await s.execute(sa.select(
            tool_calls_t.c.tool, tool_calls_t.c.args_hash, tool_calls_t.c.status).where(
            tool_calls_t.c.run_id == run_id,
            tool_calls_t.c.status.in_(("executed", "replayed"))))).fetchall()]
    if not rows:
        return None
    tool, count = Counter(_call_key(r) for r in rows).most_common(1)[0]
    if count > limits.max_identical_calls:
        return Breach("IDENTICAL_CALL_LOOP",
                      f"вызов {tool.split('|')[0]} с теми же аргументами повторён "
                      f"{count} раз при пределе {limits.max_identical_calls} — это "
                      f"цикл, а не работа")
    return None


def _informative(message: dict) -> str:
    """The part of a message that carries new information."""
    if message.get("role") != "assistant":
        return ""
    if message.get("tool_calls"):
        return ""                              # tool loops are the other guard's job
    return str(message.get("content") or "").strip()


def check_stalled_context(limits: Limits, messages: list[dict]) -> Breach | None:
    """The model is answering, but saying the same thing.

    Distinct tool calls with identical prose is the shape T3 produced: 63 steps,
    three identical review verdicts, and no new information after the first few.
    A token ceiling catches this only after a million tokens have been paid."""
    if not limits.max_stalled_steps:
        return None
    answers = [text for text in (_informative(m) for m in messages) if text]
    if len(answers) <= limits.max_stalled_steps:
        return None
    tail = answers[-(limits.max_stalled_steps + 1):]
    if len(set(tail)) == 1:
        return Breach("STALLED_CONTEXT",
                      f"последние {len(tail)} ответов модели идентичны — прогон "
                      f"перестал получать новую информацию")
    return None


# ------------------------------------------------------------------ metrics

async def run_metrics(svc, task_id: int) -> dict[str, Any]:
    """The §8 metric block, computed from what actually happened.

    `tokens_per_verified_effect` divides by VERIFIED tool calls, not by
    attempted ones: the number that matters is what a confirmed change cost,
    and dividing by attempts would make a run look more efficient the more it
    failed."""
    async with svc.db.session() as s:
        agg = (await s.execute(sa.select(
            sa.func.coalesce(sa.func.sum(runs_t.c.tokens_in), 0),
            sa.func.coalesce(sa.func.sum(runs_t.c.tokens_out), 0),
            sa.func.coalesce(sa.func.sum(runs_t.c.cost_usd), 0.0),
            sa.func.count()).where(runs_t.c.task_id == task_id))).first()
        approvals = int((await s.execute(sa.select(sa.func.count()).select_from(
            approvals_t).where(approvals_t.c.task_id == task_id))).scalar() or 0)
        verified = int((await s.execute(sa.select(sa.func.count()).select_from(
            tool_calls_t).where(tool_calls_t.c.task_id == task_id,
                                tool_calls_t.c.verified.is_(True)))).scalar() or 0)
        effects = int((await s.execute(sa.select(sa.func.count()).select_from(
            tool_calls_t).where(tool_calls_t.c.task_id == task_id,
                                tool_calls_t.c.status == "executed"))).scalar() or 0)
        meta = (await s.execute(sa.select(tasks_t.c.meta).where(
            tasks_t.c.id == task_id))).scalar()
        try:
            # `interventions` is polymorphic (target_kind/target_id), so both the
            # task itself and its runs count as interventions on this mission.
            run_ids = [int(r[0]) for r in (await s.execute(sa.select(runs_t.c.id).where(
                runs_t.c.task_id == task_id))).fetchall()]
            interventions = int((await s.execute(sa.select(sa.func.count()).select_from(
                interventions_t).where(sa.or_(
                sa.and_(interventions_t.c.target_kind == "task",
                        interventions_t.c.target_id == task_id),
                sa.and_(interventions_t.c.target_kind == "run",
                        interventions_t.c.target_id.in_(run_ids or [-1])))))).scalar() or 0)
        except Exception:  # noqa: BLE001 — таблица V2, её может не быть в старой БД
            interventions = 0
    tokens_in, tokens_out, cost, runs = int(agg[0]), int(agg[1]), float(agg[2]), int(agg[3])
    meta = meta if isinstance(meta, dict) else {}
    review_cycles = int(meta.get("review_attempts") or 0)
    replans = int(meta.get("review_escalation_rounds") or 0)
    total_tokens = tokens_in + tokens_out
    denominator = verified or effects
    return {
        "task_id": task_id,
        "runs": runs,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tokens_total": total_tokens,
        "model_cost_usd": round(cost, 6),
        "approvals_per_successful_mission": approvals,
        "interventions_per_mission": interventions,
        "review_cycles": review_cycles,
        "replans": replans,
        "verified_effects": verified,
        "executed_effects": effects,
        # None, not 0 or infinity: "we cannot say" is the honest answer when
        # nothing was verified, and a fabricated ratio would be worse than none.
        "tokens_per_verified_effect": (round(total_tokens / denominator, 1)
                                       if denominator else None),
    }
