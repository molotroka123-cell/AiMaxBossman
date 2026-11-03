"""Free cloud pool rotation (owner table 03.11: «Пул бесплатных облачных моделей — PARTIAL: NIM Ultra 503 ночью,
kimi-k3 410, Gemini free 429 после ~15 вызовов; авто-ротации нет»).

When an opted-in agent's model and its configured fallback both fail with a *transient-ish* class (throttled 429/402,
provider_down 5xx/410, timeout, silent/empty answer), the call is retried on the next FREE cloud model instead of
failing the run: free = cloud, pricing known, price_in == price_out == 0. Each failure is recorded as a health
observation, so the model goes into its exponential cooldown (model_health.cooldown_for) and is skipped by the next
call until it recovers. Owner-action failures (401/403 auth) are not rotated through — a rejected key is not fixed by
asking a different model, and re-sending keys gets them blocked.

Pure helpers here; the engine hook is `Engine._free_pool_rotate`. Opt-in per agent: permissions.free_pool is True."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from . import model_health as mh

#: failure classes worth trying another free model for
ROTATE_ON = frozenset({mh.THROTTLED, mh.PROVIDER_DOWN, mh.TIMEOUT, mh.SILENT, mh.MALFORMED})
#: never try more than this many pool models in one call (bounded cost/latency)
MAX_POOL_TRIES = 4


def is_free_cloud(row: Mapping[str, Any], *, is_local: Callable[[Mapping[str, Any]], bool]) -> bool:
    """A model costs nothing per token and is not local."""
    if is_local(row):
        return False
    if not row.get("pricing_known"):
        return False
    return float(row.get("price_in") or 0) == 0.0 and float(row.get("price_out") or 0) == 0.0


def should_rotate(status: str) -> bool:
    return status in ROTATE_ON


def pool_order(candidates: Iterable[tuple[int, mh.HealthRecord]], *, exclude: Iterable[int] = (),
               now: datetime | None = None) -> list[int]:
    """Free models worth trying, best first: proven-healthy, then unmeasured; cooling or broken ones are skipped."""
    skip = {int(x) for x in exclude}
    viable = [(mid, rec) for mid, rec in candidates
              if int(mid) not in skip and not rec.in_cooldown(now) and rec.rank_key(now)[0] <= 1]
    return [int(mid) for mid in mh.rank(viable, now)]


@dataclass
class RotationResult:
    result: Any = None
    model_id: int | None = None
    tried: list[tuple[int, str, str]] = field(default_factory=list)   # (model_id, status, detail)

    @property
    def answered(self) -> bool:
        return self.model_id is not None


async def rotate(order: Sequence[int], call: Callable[[int], Awaitable[Any]],
                 on_failure: Callable[[int, str, str], Awaitable[None]],
                 *, classify: Callable[[BaseException], tuple[str, str]] = mh.classify_exception,
                 answered_ok: Callable[[Any], bool] = lambda r: True,
                 max_tries: int = MAX_POOL_TRIES) -> RotationResult:
    """Try `order` until one model answers. `call` raises on a provider failure; an answer that `answered_ok` rejects
    (empty text) counts as SILENT. Every failure is reported through `on_failure` (the caller records health = cooldown).
    Stops early on a failure class that another model cannot fix (auth)."""
    out = RotationResult()
    for model_id in list(order)[:max(0, max_tries)]:
        try:
            result = await call(model_id)
        except Exception as exc:  # noqa: BLE001 - every provider failure is classified, nothing is swallowed silently
            status, detail = classify(exc)
            out.tried.append((model_id, status, detail))
            await on_failure(model_id, status, detail)
            if not should_rotate(status):
                break
            continue
        if not answered_ok(result):
            out.tried.append((model_id, mh.SILENT, "empty answer"))
            await on_failure(model_id, mh.SILENT, "empty answer")
            continue
        out.result, out.model_id = result, model_id
        return out
    return out
