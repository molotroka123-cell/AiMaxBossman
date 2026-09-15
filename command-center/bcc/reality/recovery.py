"""Strategy-changing recovery: try something DIFFERENT, not the same thing again.

`engine._handle_failure` re-queued the identical request — same model, same
messages, same route — with exponential backoff, then failed. That is the right
answer for exactly one failure class (a transport blip) and the wrong answer for
every other one. The acceptance corpus shows the cost: an expired provider key
burned every retry before a human noticed, because no amount of waiting fixes a
rejected credential.

The ladder replaces "retry N times" with "try a materially different path":

    same route (transient only)
      -> alternate model (the agent's fallback, or a measured-healthy one)
        -> degraded path (streaming off, tools off — capability, not authority)
          -> owner escalation

Two properties make this safe rather than merely persistent:

  * A rung is spent when attempted and never comes back, so a failing task
    reaches a terminal state in a bounded number of steps instead of alternating
    between two broken paths forever. The one exception is `retry_same`, which
    is not an idea to try but the owner's own `max_retries` budget: it stays
    available until that budget is spent, because burning it entirely on the
    first failure would make a genuinely flaky provider fail after one attempt.
  * No rung buys authority. Changing model or turning streaming off changes
    HOW a request is made, never WHAT it is permitted to do: the same
    permissions, the same approval scope, the same evidence gates apply on
    every rung. A ladder that could escalate privilege would be a bypass with
    a friendly name.

Which rungs apply is decided by the failure CLASS, because the remedies differ:
a rejected key is not fixed by waiting, a 429 is not fixed by switching models
if the whole account is throttled, and a context-length error is not fixed by
retrying at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .. import model_health as mh

# --- failure classes -------------------------------------------------------

TRANSIENT = "transient"          # connection reset, timeout, 5xx
THROTTLED = "throttled"          # 429, quota, insufficient credit
UNAUTHORIZED = "unauthorized"    # 401/403 — the owner's key or config
CAPABILITY = "capability"        # the model cannot do what was asked
CONTEXT = "context"              # prompt too long for this model
SILENT = "silent"                # 2xx with nothing usable in it
UNKNOWN = "unknown"

# --- rungs -----------------------------------------------------------------

RETRY_SAME = "retry_same"
ALTERNATE_MODEL = "alternate_model"
DEGRADED_PATH = "degraded_path"
HUMAN = "human_escalation"

#: Ladders per failure class. The ORDER is the claim: what is worth trying
#: next, given what just went wrong.
LADDERS: dict[str, tuple[str, ...]] = {
    # A blip is the one case where the same request is the right next move.
    TRANSIENT: (RETRY_SAME, ALTERNATE_MODEL, HUMAN),
    # Waiting helps a per-model limit; it does not help an exhausted account,
    # so another model is tried before giving the owner the problem.
    THROTTLED: (ALTERNATE_MODEL, RETRY_SAME, HUMAN),
    # No amount of retrying fixes a rejected key. Straight to the owner —
    # re-sending it is also how keys get blocked.
    UNAUTHORIZED: (HUMAN,),
    # The model cannot do this. A different one might; the same one will not.
    CAPABILITY: (ALTERNATE_MODEL, DEGRADED_PATH, HUMAN),
    # A prompt that does not fit will not fit on the next attempt either.
    CONTEXT: (ALTERNATE_MODEL, HUMAN),
    # Silence repeats. Another model first, then a simpler request shape.
    SILENT: (ALTERNATE_MODEL, DEGRADED_PATH, HUMAN),
    # Unclassified: one cautious repeat, then change something, then ask.
    UNKNOWN: (RETRY_SAME, ALTERNATE_MODEL, HUMAN),
}

#: Failure classes that map onto a model-health status, so a failing model is
#: recorded as unhealthy rather than merely retried.
HEALTH_STATUS = {
    TRANSIENT: mh.PROVIDER_DOWN,
    THROTTLED: mh.THROTTLED,
    UNAUTHORIZED: mh.UNAUTHORIZED,
    SILENT: mh.SILENT,
    CAPABILITY: mh.MALFORMED,
    CONTEXT: mh.MALFORMED,
}

_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (UNAUTHORIZED, ("401", "403", "unauthorized", "api key", "invalid key",
                    "expired", "authentication", "forbidden")),
    (THROTTLED, ("429", "402", "rate limit", "rate_limit", "quota", "too many requests",
                 "insufficient", "credit")),
    (CONTEXT, ("context length", "context_length", "maximum context",
               "too many tokens", "prompt is too long")),
    (CAPABILITY, ("does not support", "not supported", "unsupported",
                  "no endpoints found", "tool use", "tool_choice")),
    (SILENT, ("empty response", "empty_response", "no content", "пустот")),
    (TRANSIENT, ("timeout", "timed out", "connection", "reset", "unavailable",
                 "502", "503", "504", "500", "network")),
)


def classify_failure(error: str, *, kind: str | None = None) -> str:
    """Name the failure so the ladder can pick a remedy that fits it.

    Ordered most-specific first: a 429 mentioning "connection pool" is
    throttling, not a transport blip, and treating it as the latter would
    prescribe exactly the retry that keeps the limit exhausted."""
    text = str(error or "").lower()
    for label, markers in _MARKERS:
        if any(marker in text for marker in markers):
            return label
    if kind == "network":
        return TRANSIENT
    return UNKNOWN


#: Strategy changes a run may make in its whole life, across ALL failure
#: classes: one model switch and one degraded path. Together with the owner's
#: `max_retries` (same-route repeats) this is the global recovery budget:
#:
#:     transitions <= max_retries + MAX_STRATEGY_CHANGES, then human escalation
#:
#: Astra/Codex F6 (2026-09-08, 2/2, 24 persisted transitions): `from_dict`
#: discarded the spent rungs whenever the failure CLASS changed, so a provider
#: alternating "unsupported tool use" / "empty response" re-earned the degraded
#: path on every transition and the run stayed `queued` indefinitely. A path
#: that was tried is tried, whatever the label on the next error.
MAX_STRATEGY_CHANGES = 2
STRATEGY_RUNGS = (ALTERNATE_MODEL, DEGRADED_PATH)


def recovery_budget(max_retries: int) -> int:
    """Total failure transitions a run may take before it MUST go to the owner."""
    return max(0, int(max_retries)) + MAX_STRATEGY_CHANGES


@dataclass
class Ladder:
    """Which rungs remain for this run. Spent rungs never come back (see
    `spend` for the one deliberate exception) — not even when the next failure
    wears a different class: `spent` is the run's history, not the class's."""
    failure_class: str
    spent: tuple[str, ...] = ()
    transitions: int = 0                    # failure handlings so far, all classes
    classes: tuple[str, ...] = ()           # every failure class seen, in order

    @property
    def available(self) -> tuple[str, ...]:
        return tuple(r for r in LADDERS.get(self.failure_class, LADDERS[UNKNOWN])
                     if r not in self.spent)

    def spend(self, rung: str) -> "Ladder":
        """Mark a rung used. RETRY_SAME is the exception and stays available.

        It is not "one more idea to try" — it is the owner's own `max_retries`
        budget, and `next_rung` already gates it on how much of that budget is
        left. Burning the whole budget on the first failure would make a
        genuinely flaky provider fail after one attempt, which is a resilience
        regression wearing a state machine's clothes."""
        if rung == RETRY_SAME:
            return self
        return Ladder(self.failure_class, tuple(self.spent) + (rung,),
                      self.transitions, self.classes)

    def to_dict(self) -> dict[str, Any]:
        return {"failure_class": self.failure_class, "spent": list(self.spent),
                "transitions": int(self.transitions), "classes": list(self.classes)}

    @classmethod
    def from_dict(cls, data: Any, failure_class: str) -> "Ladder":
        """A ladder is carried in the run checkpoint and survives a restart.

        The current failure class selects which rungs are RELEVANT; it never
        refreshes which rungs are SPENT. A different class is a different
        symptom of the same run, and the model switch / degraded path already
        tried on the previous symptom do not become untried. Every handling
        counts one transition, whatever the class."""
        if not isinstance(data, dict):
            return cls(failure_class, (), 1, (failure_class,))
        spent = data.get("spent")
        spent_t = tuple(str(r) for r in spent if isinstance(r, str)) if isinstance(spent, list) else ()
        try:
            transitions = int(data.get("transitions") or 0)
        except (TypeError, ValueError):
            transitions = 0
        classes = data.get("classes")
        classes_t = tuple(str(c) for c in classes if isinstance(c, str)) if isinstance(classes, list) else ()
        if not classes_t and isinstance(data.get("failure_class"), str):
            classes_t = (str(data["failure_class"]),)
        return cls(failure_class, spent_t, transitions + 1, classes_t + (failure_class,))

    @property
    def strategy_changes(self) -> int:
        return sum(1 for r in self.spent if r in STRATEGY_RUNGS)


@dataclass
class Rung:
    """The chosen next move, and everything the caller needs to make it."""
    name: str
    reason: str
    ladder: Ladder
    model_id: int | None = None
    degrade: dict[str, Any] = field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        return self.name == HUMAN

    def to_dict(self) -> dict[str, Any]:
        return {"rung": self.name, "reason": self.reason, "model_id": self.model_id,
                "degrade": dict(self.degrade), "ladder": self.ladder.to_dict()}


def next_rung(ladder: Ladder, *, current_model_id: int | None,
              fallback_model_id: int | None = None,
              healthy_models: Sequence[tuple[int, mh.HealthRecord]] = (),
              retries_left: int = 0, max_retries: int | None = None) -> Rung:
    """Pick the next rung, or escalate to the owner when nothing is left.

    Escalation is a real answer, not a failure of this function: handing a
    problem to a human beats cycling through paths that cannot work.

    Two bounds hold regardless of how the failure classes alternate: the spent
    rungs persist across classes (see `Ladder.from_dict`), and the total number
    of transitions may not exceed `recovery_budget(max_retries)`. The second is
    the belt to the first's braces — no sequence of labels can exceed it."""
    budget = recovery_budget(max_retries if max_retries is not None else retries_left)
    if ladder.transitions > budget or ladder.strategy_changes >= MAX_STRATEGY_CHANGES and retries_left <= 0:
        return Rung(HUMAN, _escalation_reason(ladder, retries_left=retries_left,
                                              budget_exhausted=True),
                    ladder.spend(HUMAN))
    for candidate in ladder.available:
        if candidate == RETRY_SAME:
            if retries_left <= 0:
                continue                       # the owner's retry budget is spent
            return Rung(RETRY_SAME, "повтор того же маршрута: сбой похож на разовый",
                        ladder.spend(RETRY_SAME))
        if candidate == ALTERNATE_MODEL:
            model_id = _pick_alternate(current_model_id, fallback_model_id, healthy_models)
            if model_id is None:
                continue                       # nothing else to switch to
            return Rung(ALTERNATE_MODEL,
                        f"другая модель ({model_id}): текущая не справилась с "
                        f"{ladder.failure_class}",
                        ladder.spend(ALTERNATE_MODEL), model_id=model_id)
        if candidate == DEGRADED_PATH:
            return Rung(DEGRADED_PATH,
                        "упрощённый путь: без стрима и без инструментов — "
                        "меньше требований к модели, те же полномочия",
                        ladder.spend(DEGRADED_PATH),
                        degrade={"stream": False, "tools": False})
        if candidate == HUMAN:
            break
    return Rung(HUMAN, _escalation_reason(ladder, retries_left=retries_left),
                ladder.spend(HUMAN))


def _escalation_reason(ladder: Ladder, *, retries_left: int = 0,
                       budget_exhausted: bool = False) -> str:
    if ladder.failure_class == UNAUTHORIZED and not budget_exhausted:
        return ("ключ или доступ провайдера отвергнут — это решает владелец; "
                "повторные попытки только блокируют ключ")
    # `retry_same` не попадает в `spent` (это бюджет владельца, а не ступень),
    # поэтому перечислять только `spent` было бы неправдой: сообщение
    # «испробовано: нет» после трёх повторов вводит владельца в заблуждение.
    tried = list(ladder.spent)
    if not retries_left and RETRY_SAME in LADDERS.get(ladder.failure_class, ()):
        tried.insert(0, "повторы того же маршрута (бюджет исчерпан)")
    seen = [c for i, c in enumerate(ladder.classes) if c not in ladder.classes[:i]]
    classes = ", ".join(seen) if len(seen) > 1 else ladder.failure_class
    head = ("бюджет восстановления исчерпан" if budget_exhausted
            else "автоматические пути исчерпаны")
    return (f"{head} (сбоев: {ladder.transitions}; класс сбоя: {classes}; "
            f"испробовано: {', '.join(tried) or 'нечего было пробовать'}) — "
            f"решение владельцу")


def _pick_alternate(current: int | None, fallback: int | None,
                    healthy: Sequence[tuple[int, mh.HealthRecord]]) -> int | None:
    """The agent's declared fallback first — the owner configured it — then the
    best measured-healthy model that is not the one that just failed."""
    if fallback is not None and fallback != current:
        return int(fallback)
    pool = [(mid, rec) for mid, rec in healthy if mid != current]
    return mh.select_fallback(pool) if pool else None
