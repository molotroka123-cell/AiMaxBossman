"""Stage 9 — Resource Brain bounded stress: конкурентные аренды, исчерпание,
release, TTL expiry, restart-восстановление. Детерминированная fake-ёмкость,
никакого реального железа и никаких бесконечных нагрузок.

Семантика: давление = 1 - (unified_available - held - request) / ram_total;
max_ram_pressure задаёт ПОЛ свободной памяти (0.1 => держать >=90% ram_total).
"""
import asyncio

import pytest

from bossman.resource_brain import ResourceBrain, ResourceSnapshot, WorkloadRequest


def _snap(ram_total=1_000, ram_available=1_000, disk_total=100_000, disk_free=100_000):
    return ResourceSnapshot(ram_total, ram_available, disk_total, disk_free)


def _brain(**kw) -> ResourceBrain:
    # пол свободной памяти = 90% от ram_total => бюджет удержания = 10% = 100 ед.
    return ResourceBrain(max_ram_pressure=0.1, disk_reserve=100, **kw)


def test_stage9_concurrent_leases_no_overcommit():
    """Пять acquire подряд: ровно 3 влезают в бюджет 100 ед., overcommit невозможен."""
    brain = _brain()
    snap = _snap()
    acquired = 0
    for i in range(5):
        try:
            brain.acquire(WorkloadRequest(kind="test", estimated_ram=30), snap=snap)
            acquired += 1
        except Exception:
            break
    assert acquired == 3                      # 30*3=90 <= 100, четвёртый уже нет
    held_ram = brain.held()[0]
    assert held_ram == 90 and held_ram <= 100


def test_stage9_exhaustion_denies_then_release_allows():
    from bossman.errors import ResourceExhausted
    brain = _brain()
    snap = _snap()
    l1 = brain.acquire(WorkloadRequest(kind="test", estimated_ram=70), snap=snap)
    with pytest.raises(ResourceExhausted):
        brain.acquire(WorkloadRequest(kind="test", estimated_ram=70), snap=snap)
    assert brain.release(l1.id) is True
    l2 = brain.acquire(WorkloadRequest(kind="test", estimated_ram=70), snap=snap)
    assert l2 is not None


def test_stage9_double_release_safe():
    brain = _brain()
    l1 = brain.acquire(WorkloadRequest(kind="test", estimated_ram=10), snap=_snap())
    assert brain.release(l1.id) is True
    assert brain.release(l1.id) is False      # повторный release не бросает и не врёт


def test_stage9_ttl_expiry_sweeps_stale_lease():
    """TTL истёк по управляемым часам → sweep возвращает lease, память снова доступна."""
    from bossman.resource_brain.ledger import LeaseLedger
    t = {"now": 1000.0}
    brain = ResourceBrain(max_ram_pressure=0.1, disk_reserve=100,
                          ledger=LeaseLedger(clock=lambda: t["now"]))
    snap = _snap()
    l1 = brain.acquire(WorkloadRequest(kind="test", estimated_ram=50), snap=snap, ttl=60.0)
    t["now"] += 61.0                          # время ушло вперёд за TTL
    swept = brain.sweep()
    assert any(s.id == l1.id for s in swept)
    # после sweep память снова доступна
    l2 = brain.acquire(WorkloadRequest(kind="test", estimated_ram=90), snap=snap)
    assert l2 is not None


def test_stage9_recovery_after_restart_no_stale_leak():
    """«Перезапуск»: свежий brain начинает с пустыми арендами — утечки нет,
    ёмкость полностью доступна."""
    brain = _brain()
    snap = _snap()
    from bossman.resource_brain.ledger import LeaseLedger
    t = {"now": 0.0}
    aging = ResourceBrain(max_ram_pressure=0.1, disk_reserve=100,
                          ledger=LeaseLedger(clock=lambda: t["now"]))
    l = aging.acquire(WorkloadRequest(kind="test", estimated_ram=60), snap=snap, ttl=60.0)
    t["now"] += 61.0
    assert any(s.id == l.id for s in aging.sweep())
    fresh = _brain()
    assert fresh.held() == (0, 0)
    l2 = fresh.acquire(WorkloadRequest(kind="test", estimated_ram=90), snap=snap)
    assert l2 is not None


def test_stage9_pressure_and_disk_reserve_respected():
    brain = _brain()
    low = _snap(ram_total=1000, ram_available=20)          # проекция давления > 0.1
    d = brain.admit(low, WorkloadRequest(kind="test", estimated_ram=1000))
    assert d.allowed is False and d.reason == "ram_pressure"
    tight_disk = _snap(disk_free=150)                      # disk_reserve=100 не даёт
    d2 = brain.admit(tight_disk, WorkloadRequest(kind="test", estimated_ram=10,
                                                 estimated_disk=100))
    assert d2.allowed is False and d2.reason == "disk_reserve"
