"""On-demand ACE-Step start for Music Studio (master prompt 28.10, task 1a).

When the owner presses «Сгенерировать» and ACE-Step (127.0.0.1:8001) is installed but not running, start it under a
memory lease instead of failing with «не запущен». Every condition is explicit and reported, never silent:
  * Resource Brain policy `enforce` must be true (it is false by default: then nothing is started on its own);
  * the install must be complete (no download, no install here);
  * at least MIN_FREE_GB of free memory by a live reading (an unknown reading counts as not enough);
  * the lease ledger must GRANT a lease for the `acestep` consumer (heavy models run one at a time; a queued or
    refused lease is reported and nothing is started);
  * the service is started through the existing `start_service` (single instance, loopback only) and awaited until
    /health says models_initialized.
Release of the lease: the lease is taken for LEASE_TTL_S (no heartbeat loop here) and released at once if the start fails.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

MIN_FREE_GB = 24.0
READY_TIMEOUT_S = 300.0
POLL_S = 3.0
LEASE_TTL_S = 900.0
CONSUMER = "acestep"


class _Deps:
    """Real wiring, imported lazily (music_studio imports this module)."""

    def __init__(self, request):
        self.request = request
        self.svc = request.app.state.svc

    async def health(self) -> dict:
        from . import music_studio as ms
        return await ms._probe_health(ms._data_dir(self.request))

    async def enforce(self) -> bool:
        from . import resources
        return bool((await resources._policy(self.svc)).get("enforce"))

    def free_gb(self) -> float | None:
        from . import resource_leases as rl
        return rl._available_gb()

    async def lease(self) -> dict:
        from . import resource_leases as rl
        led = await rl._ledger(self.request)
        return await asyncio.to_thread(led.request, CONSUMER, "background", "music-studio:auto-start", ttl_s=LEASE_TTL_S)

    async def release(self, lease: dict) -> None:
        from . import resource_leases as rl
        led = await rl._ledger(self.request)
        await asyncio.to_thread(led.release, lease.get("id"), lease.get("token"), force=False)

    async def start(self) -> dict:
        from . import music_studio as ms
        data_dir = ms._data_dir(self.request)
        if data_dir is None:
            return {"ok": False, "message": "не задан каталог данных Bossman"}
        try:
            return await ms.start_service(data_dir)
        except Exception as exc:  # HTTPException from start_service carries the honest reason in .detail
            detail = getattr(exc, "detail", None)
            msg = detail.get("message") if isinstance(detail, dict) else str(detail or exc)
            return {"ok": False, "message": msg}


async def ensure_acestep(request, *, deps: Any = None, sleep=asyncio.sleep, clock=time.monotonic) -> dict[str, Any]:
    """Return {"state": running|started|skipped|refused|failed, "reason": ..., ...}. Never raises for expected conditions."""
    d = deps or _Deps(request)
    state = await d.health()
    status = state.get("status")
    if status == "READY":
        return {"state": "running"}
    waiting_for_own_start = status in ("STARTING", "NOT_READY")
    if not waiting_for_own_start:
        if status != "NOT_RUNNING":
            return {"state": "skipped", "reason": state.get("reason") or f"статус {status}", "status": status}
        if not await d.enforce():
            return {"state": "skipped", "reason": "Resource Brain: enforce=false — автозапуск тяжёлой модели отключён "
                                                  "(включите enforce в «Ресурсах» или запустите ACE-Step кнопкой)"}
        free = d.free_gb()
        if free is None or free < MIN_FREE_GB:
            return {"state": "refused", "reason": (f"свободно {free:.1f} ГБ памяти, нужно ≥ {MIN_FREE_GB:.0f} ГБ" if free is not None
                                                   else "свободная память не измерена — автозапуск не разрешён"),
                    "free_gb": free}
        lease = await d.lease()
        if lease.get("state") != "granted":
            return {"state": "refused", "reason": lease.get("reason") or f"аренда памяти: {lease.get('state')}",
                    "lease_state": lease.get("state")}
        started = await d.start()
        if not started.get("ok"):
            await d.release(lease)
            return {"state": "failed", "reason": started.get("message") or "ACE-Step не запустился", "lease": "released"}
    else:
        lease = None
    t0 = clock()
    while clock() - t0 < READY_TIMEOUT_S:
        if (await d.health()).get("status") == "READY":
            return {"state": "started", "waited_s": round(clock() - t0, 1), "lease_id": (lease or {}).get("id")}
        await sleep(POLL_S)
    if lease is not None:
        await d.release(lease)
    return {"state": "failed", "reason": f"ACE-Step не стал готов за {READY_TIMEOUT_S:.0f} с (модели ещё грузятся)",
            "lease": "released" if lease is not None else None}
