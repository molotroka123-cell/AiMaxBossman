"""B5 — a model that answers nothing is not healthy.

Cloud QA pinned `cohere/north-mini-code:free`, probed it, got empty responses,
and the model still counted as usable. Two separate mistakes produced that:

  * `Registry.check_model` asked the PROVIDER's health endpoint. A reachable
    endpoint says the provider is up; it says nothing about whether this model
    returns anything. Endpoint discovery is not capability.
  * `Registry.test_model` marked a model `online` whenever the chat call did
    not raise. A 200 carrying an empty string does not raise, so silence read
    as success.

This module is the classifier those two paths were missing. It answers with a
*named* status rather than a boolean, because the router needs different
behaviour for each:

  healthy        produced a usable answer — the only status that permits routing
  silent         2xx with an empty or whitespace answer
  malformed      2xx whose body could not be understood
  throttled      429 / quota exhausted
  unauthorized   401 / 403 — the owner's credentials or config, not the model
  provider_down  5xx or a transport failure
  timeout        no answer inside the budget
  unmeasured     never probed. NOT healthy and NOT unhealthy.

`unmeasured` is deliberately its own status and deliberately ranks BELOW
healthy. The gateway used to sort "unchecked targets [as] optimistically
usable", which is how a silent model wins a race against a proven one. Unknown
is not a convenient default.

Transient failures (throttled, provider_down, timeout) get an exponential
cooldown and are re-probed. Persistent ones (silent, malformed) get a longer
cooldown because a model that answers nothing will keep answering nothing, and
re-asking it every minute is how the free tier ate the probe budget.
`unauthorized` never auto-retries at all: only the owner can fix it, and
hammering a rejected key is how keys get blocked.

Confidence is a BAND, not a decimal. Two observations do not justify "0.87";
saying `low` is the honest summary and cannot be mistaken for a measurement.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

HEALTHY = "healthy"
SILENT = "silent"
MALFORMED = "malformed"
THROTTLED = "throttled"
UNAUTHORIZED = "unauthorized"
PROVIDER_DOWN = "provider_down"
TIMEOUT = "timeout"
UNMEASURED = "unmeasured"

#: Failures that say "not right now". Re-probed with exponential backoff.
TRANSIENT = frozenset({THROTTLED, PROVIDER_DOWN, TIMEOUT})
#: Failures that say "not like this". A model that returns nothing will keep
#: returning nothing, so the cooldown is long rather than aggressive.
PERSISTENT = frozenset({SILENT, MALFORMED})
#: Needs a human. Retrying a rejected key is how keys get blocked.
OWNER_ACTION = frozenset({UNAUTHORIZED})

BASE_COOLDOWN_SECONDS = 60
PERSISTENT_COOLDOWN_SECONDS = 900
MAX_COOLDOWN_SECONDS = 3600
#: Beyond this, extra consecutive failures do not lengthen the wait further —
#: an unbounded backoff is indistinguishable from removing the model.
MAX_BACKOFF_STEPS = 6

#: A measurement older than this is history, not current health.
STALE_AFTER_SECONDS = 1800

#: Probe budgets. First byte is separate from the total on purpose: a provider
#: that accepts the connection and then goes quiet is the exact failure a total
#: timeout notices far too late.
DEFAULT_FIRST_BYTE_TIMEOUT = 20.0
DEFAULT_TOTAL_TIMEOUT = 60.0

CONFIDENCE_UNKNOWN, CONFIDENCE_LOW = "unknown", "low"
CONFIDENCE_MEDIUM, CONFIDENCE_HIGH = "medium", "high"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@dataclass(slots=True)
class HealthRecord:
    """What is actually known about one model, and how well it is known."""
    status: str = UNMEASURED
    detail: str = ""
    checked_at: datetime | None = None
    last_success_at: datetime | None = None
    consecutive_failures: int = 0
    successes: int = 0
    samples: int = 0
    cooldown_until: datetime | None = None
    latency_ms: int | None = None

    # ---------------------------------------------------------------- derived

    @property
    def confidence(self) -> str:
        """A band, never a decimal. Two probes do not justify "0.87"."""
        if not self.samples or self.checked_at is None:
            return CONFIDENCE_UNKNOWN
        age = (_now() - _aware(self.checked_at)).total_seconds()
        if age > STALE_AFTER_SECONDS:
            return CONFIDENCE_LOW               # we knew, a while ago
        if self.samples >= 5:
            return CONFIDENCE_HIGH
        if self.samples >= 2:
            return CONFIDENCE_MEDIUM
        return CONFIDENCE_LOW

    @property
    def stale(self) -> bool:
        if self.checked_at is None:
            return True
        return (_now() - _aware(self.checked_at)).total_seconds() > STALE_AFTER_SECONDS

    def in_cooldown(self, now: datetime | None = None) -> bool:
        until = _aware(self.cooldown_until)
        return until is not None and (now or _now()) < until

    def usable(self, now: datetime | None = None) -> bool:
        """Route to this model? Only a measured success, outside its cooldown.

        `unmeasured` is NOT usable here — but see `rank_key`: it is still
        preferred over a model measured to be broken, so a fresh install is not
        deadlocked waiting for probes it never runs."""
        return self.status == HEALTHY and not self.in_cooldown(now)

    def rank_key(self, now: datetime | None = None) -> tuple[int, float]:
        """Lower sorts first: measured-healthy, then unmeasured, then broken.

        Unmeasured sitting between the two is the whole point of B5. Ranking it
        with healthy ("optimistically usable") lets a silent model beat a proven
        one; ranking it with broken means a never-probed model can never be
        tried and health never gets measured."""
        if self.usable(now):
            tier = 0
        elif self.status == UNMEASURED:
            tier = 1
        elif self.in_cooldown(now):
            tier = 3
        else:
            tier = 2
        return tier, float(self.consecutive_failures)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "detail": self.detail[:500],
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
            "last_success_at": (self.last_success_at.isoformat()
                                if self.last_success_at else None),
            "consecutive_failures": self.consecutive_failures,
            "successes": self.successes,
            "samples": self.samples,
            "cooldown_until": (self.cooldown_until.isoformat()
                               if self.cooldown_until else None),
            "latency_ms": self.latency_ms,
            "confidence": self.confidence,
            "usable": self.usable(),
            "stale": self.stale,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "HealthRecord":
        """Tolerant by design: a record this code cannot read is `unmeasured`,
        which is safe. Guessing `healthy` from a corrupt row would not be."""
        if not isinstance(data, dict):
            return cls()

        def _dt(key: str) -> datetime | None:
            raw = data.get(key)
            if not isinstance(raw, str) or not raw:
                return None
            try:
                return _aware(datetime.fromisoformat(raw))
            except ValueError:
                return None

        def _int(key: str) -> int:
            try:
                return max(0, int(data.get(key) or 0))
            except (TypeError, ValueError):
                return 0

        status = str(data.get("status") or UNMEASURED)
        if status not in ALL_STATUSES:
            status = UNMEASURED
        latency = data.get("latency_ms")
        return cls(status=status, detail=str(data.get("detail") or ""),
                   checked_at=_dt("checked_at"), last_success_at=_dt("last_success_at"),
                   consecutive_failures=_int("consecutive_failures"),
                   successes=_int("successes"), samples=_int("samples"),
                   cooldown_until=_dt("cooldown_until"),
                   latency_ms=int(latency) if isinstance(latency, (int, float)) else None)


ALL_STATUSES = frozenset({HEALTHY, SILENT, MALFORMED, THROTTLED, UNAUTHORIZED,
                          PROVIDER_DOWN, TIMEOUT, UNMEASURED})


def classify_answer(text: Any, *, tokens_out: int | None = None) -> tuple[str, str]:
    """A 2xx response is not yet an answer.

    This is the check that was missing: `cohere/north-mini-code:free` returned
    HTTP 200 with an empty body and was recorded as online."""
    if text is None:
        return MALFORMED, "провайдер вернул ответ без текстового поля"
    if not isinstance(text, str):
        return MALFORMED, f"текст ответа имеет тип {type(text).__name__}, а не строку"
    if not text.strip():
        # `tokens_out` is reported by the provider, so a zero here corroborates
        # the empty body rather than being a second guess at the same thing.
        extra = f" (tokens_out={tokens_out})" if tokens_out is not None else ""
        return SILENT, f"модель ответила пустотой{extra}"
    return HEALTHY, ""


def classify_status_code(status_code: int) -> tuple[str, str]:
    if 200 <= status_code < 300:
        return HEALTHY, ""
    if status_code in (401, 403):
        return UNAUTHORIZED, f"HTTP {status_code}: ключ или доступ провайдера"
    if status_code == 429:
        return THROTTLED, f"HTTP {status_code}: лимит запросов или квота"
    if status_code == 402:
        return THROTTLED, f"HTTP {status_code}: недостаточно средств у провайдера"
    if status_code >= 500:
        return PROVIDER_DOWN, f"HTTP {status_code}: ошибка провайдера"
    return MALFORMED, f"HTTP {status_code}: запрос отвергнут провайдером"


def classify_exception(exc: BaseException) -> tuple[str, str]:
    name = type(exc).__name__
    text = str(exc)
    if "Timeout" in name or "timed out" in text.lower():
        return TIMEOUT, f"{name}: {text}"[:300]
    kind = getattr(exc, "kind", None)
    if kind == "auth":
        return UNAUTHORIZED, f"{name}: {text}"[:300]
    if kind == "rate_limit":
        return THROTTLED, f"{name}: {text}"[:300]
    return PROVIDER_DOWN, f"{name}: {text}"[:300]


def cooldown_for(status: str, consecutive_failures: int) -> timedelta:
    """How long to leave a failing model alone.

    Owner-action failures get no automatic retry at all: only the owner can fix
    a rejected key, and re-sending it is how keys get blocked."""
    if status == HEALTHY:
        return timedelta(0)
    if status in OWNER_ACTION:
        return timedelta(seconds=MAX_COOLDOWN_SECONDS)
    steps = min(max(consecutive_failures, 1), MAX_BACKOFF_STEPS) - 1
    base = PERSISTENT_COOLDOWN_SECONDS if status in PERSISTENT else BASE_COOLDOWN_SECONDS
    return timedelta(seconds=min(base * (2 ** steps), MAX_COOLDOWN_SECONDS))


def record_observation(prior: HealthRecord | None, status: str, detail: str = "",
                       *, latency_ms: int | None = None,
                       now: datetime | None = None) -> HealthRecord:
    """Fold one measurement into the record. Pure — the caller persists it."""
    at = now or _now()
    rec = prior or HealthRecord()
    out = HealthRecord(
        status=status,
        detail=detail,
        checked_at=at,
        last_success_at=at if status == HEALTHY else _aware(rec.last_success_at),
        consecutive_failures=0 if status == HEALTHY else rec.consecutive_failures + 1,
        successes=rec.successes + (1 if status == HEALTHY else 0),
        samples=rec.samples + 1,
        latency_ms=latency_ms,
    )
    cooldown = cooldown_for(status, out.consecutive_failures)
    out.cooldown_until = None if not cooldown else at + cooldown
    return out


def rank(candidates: Iterable[tuple[Any, HealthRecord]],
         now: datetime | None = None) -> list[Any]:
    """Order candidates by health: proven, then unknown, then broken."""
    return [item for item, _ in sorted(candidates, key=lambda pair: pair[1].rank_key(now))]


def select_fallback(candidates: Iterable[tuple[Any, HealthRecord]],
                    *, exclude: Iterable[Any] = (), now: datetime | None = None) -> Any | None:
    """Best next candidate, or None when nothing is worth trying.

    None is a real answer. Returning a model already known to be silent, just to
    return something, is how a failing run burns its budget on a model that
    cannot answer."""
    skip = set(exclude)
    viable = [(item, rec) for item, rec in candidates
              if item not in skip and rec.rank_key(now)[0] <= 1]
    if not viable:
        return None
    return rank(viable, now)[0]


def summary(records: dict[Any, HealthRecord]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for rec in records.values():
        counts[rec.status] = counts.get(rec.status, 0) + 1
    return {"total": len(records), "by_status": counts,
            "usable": sum(1 for r in records.values() if r.usable()),
            "unmeasured": counts.get(UNMEASURED, 0)}
