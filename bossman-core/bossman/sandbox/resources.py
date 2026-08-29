"""Stage 8 — адаптер аренд ресурсов к существующему Resource Brain (Этап 4).

Никакого второго Resource Brain: песочница получает допуск через тот же BRAIN.
Дорогая среда не создаётся без аренды. Release идемпотентен (double release
безопасен) и обязан вызываться на всех путях: успех/ошибка/отмена/снос/recovery.
"""
from __future__ import annotations

from .. import errors
from ..resource_brain import BRAIN, WorkloadRequest
from .models import SandboxSession


class ResourceLeaseAdapter:
    def __init__(self, brain=BRAIN) -> None:
        self.brain = brain
        # sandbox_id -> lease_id (единственная активная аренда на песочницу)
        self._by_sandbox: dict[str, str] = {}

    def reserve(self, session: SandboxSession, *, snap=None, ttl: float | None = None) -> str:
        """Зарезервировать ёмкость под песочницу. Бросает ResourceExhausted при
        нехватке (backpressure). Идемпотентно по песочнице: повторный reserve при
        живой аренде возвращает ту же аренду."""
        existing = self._by_sandbox.get(session.id)
        if existing:
            return existing
        req = WorkloadRequest(
            kind="sandbox",
            estimated_ram=session.spec.resources.ram_bytes,
            estimated_disk=session.spec.resources.disk_bytes,
            priority=40,
        )
        lease = self.brain.acquire(req, snap=snap, ttl=ttl, kind="sandbox")
        self._by_sandbox[session.id] = lease.id
        session.lease_id = lease.id
        return lease.id

    def release(self, session: SandboxSession) -> bool:
        """Освободить аренду песочницы. Безопасно при повторном вызове и при
        отсутствии аренды (возвращает False, не бросает)."""
        lease_id = self._by_sandbox.pop(session.id, None) or session.lease_id
        session.lease_id = None
        if not lease_id:
            return False
        try:
            return bool(self.brain.release(lease_id))
        except Exception:  # noqa: BLE001 — release не должен ронять путь очистки
            return False

    def active(self) -> dict[str, str]:
        return dict(self._by_sandbox)
