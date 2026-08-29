"""Тесты Resource Brain (Этап 4).

Покрывают: приём в рамках/сверх бюджета (контракт прототипа), устранение
P0-гонки OOM через реестр аренд (последовательно и под реальной многопоточной
нагрузкой), TTL-подметание, единый пул памяти без двойного учёта VRAM, пороги
PressureLevel, порядок rank_models и жизненный цикл подсистемы.

Все тесты детерминированы: снимок инъектируется, часы реестра фейковые, проба в
lifecycle-тесте — заглушка. Ни один тест не зависит от реального GPU/хоста.
"""
from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from bossman import errors, events
from bossman.resource_brain import (
    AdmissionDecision,
    LeaseLedger,
    ModelResidency,
    PressureLevel,
    ResourceBrain,
    ResourceLease,
    ResourceSnapshot,
    WorkloadRequest,
    ResourceBrainSubsystem,
)


class _FakeClock:
    """Управляемые монотонные часы для детерминированного TTL."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class _StubProbe:
    """Проба-заглушка: всегда отдаёт заданный снимок. Absent-safe по контракту."""

    name = "stub"

    def __init__(self, snap: ResourceSnapshot) -> None:
        self._snap = snap

    def available(self) -> bool:
        return True

    def snapshot(self, model_resident: tuple[str, ...] = ()) -> ResourceSnapshot:
        return self._snap


# --------------------------------------------------------------------------- #
# 1. Приём в рамках/сверх бюджета — зеркало приёмочного теста на уровне API.
# --------------------------------------------------------------------------- #

def test_admit_allows_within_budget_denies_over_budget():
    s = ResourceSnapshot(1000, 500, 10000, 8000)
    b = ResourceBrain(max_ram_pressure=0.8, disk_reserve=1000)

    ok = b.admit(s, WorkloadRequest(estimated_ram=100, estimated_disk=10))
    assert isinstance(ok, AdmissionDecision)
    assert ok.allowed and ok.reason == "ok"

    over = b.admit(s, WorkloadRequest(estimated_ram=400, estimated_disk=10))
    assert not over.allowed and over.reason == "ram_pressure"


def test_admit_denies_on_disk_reserve():
    s = ResourceSnapshot(1000, 900, 10000, 1500)
    b = ResourceBrain(max_ram_pressure=0.99, disk_reserve=1000)
    # 1500 - 600 = 900 < 1000 → нарушение резерва диска.
    d = b.admit(s, WorkloadRequest(estimated_ram=1, estimated_disk=600))
    assert not d.allowed and d.reason == "disk_reserve"


# --------------------------------------------------------------------------- #
# 2. P0-гонка OOM: реестр аренд держит бронь между вызовами acquire().
# --------------------------------------------------------------------------- #

def test_lease_ledger_closes_oom_race_sequential():
    snap = ResourceSnapshot(1000, 1000, 100_000, 100_000)
    b = ResourceBrain(max_ram_pressure=0.8, disk_reserve=1000)
    b.set_snapshot(snap)
    req = WorkloadRequest(kind="llm", estimated_ram=500, estimated_disk=0)

    # По отдельности каждая заявка проходит stateless-проверку.
    assert b.admit(snap, req).allowed

    lease1 = b.acquire(req)
    assert isinstance(lease1, ResourceLease) and lease1.ram == 500

    # Вторая бронь видит первую → отказ (иначе был бы OOM).
    with pytest.raises(errors.ResourceExhausted) as ei:
        b.acquire(req)
    assert ei.value.http == 503 and ei.value.retryable is True

    # Освобождение возвращает ёмкость — следующая бронь снова проходит.
    assert b.release(lease1.id) is True
    lease2 = b.acquire(req)
    assert lease2.id != lease1.id
    # Повторный release идемпотентен.
    assert b.release(lease1.id) is False


def test_lease_ledger_holds_under_thread_race():
    """Настоящая многопоточная гонка: 10 потоков хватают по 300 против пула 1000
    при потолке давления 0.8 (влезает ровно 2 брони: удержано 600, проекция 0.9
    на третьей). Реестр обязан выдать РОВНО 2, не устроив over-commit."""
    snap = ResourceSnapshot(1000, 1000, 100_000, 100_000)
    b = ResourceBrain(max_ram_pressure=0.8, disk_reserve=1000)
    b.set_snapshot(snap)
    req = WorkloadRequest(kind="llm", estimated_ram=300)

    granted: list[ResourceLease] = []
    denied = 0

    def worker():
        nonlocal denied
        try:
            return b.acquire(req)
        except errors.ResourceExhausted:
            return None

    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(lambda _: worker(), range(10)))

    for r in results:
        if r is None:
            denied += 1
        else:
            granted.append(r)

    assert len(granted) == 2, f"over-commit: выдано {len(granted)} броней вместо 2"
    assert denied == 8
    # Суммарно удержано ровно 600 ≤ пула; проекция ≤ потолка.
    held_ram, _ = b.held()
    assert held_ram == 600


# --------------------------------------------------------------------------- #
# 3. TTL-подметание освобождает протухшую бронь.
# --------------------------------------------------------------------------- #

def test_ttl_sweep_reclaims_expired_lease():
    clock = _FakeClock(100.0)
    ledger = LeaseLedger(clock=clock)
    b = ResourceBrain(max_ram_pressure=0.8, disk_reserve=1000, ledger=ledger)
    snap = ResourceSnapshot(1000, 1000, 100_000, 100_000)
    b.set_snapshot(snap)
    req = WorkloadRequest(estimated_ram=500)

    lease = b.acquire(req, ttl=10.0)
    assert b.held()[0] == 500
    # Пул занят — вторая такая же бронь не влезает.
    with pytest.raises(errors.ResourceExhausted):
        b.acquire(req, ttl=10.0)

    # Двигаем часы за TTL и подметаем.
    clock.advance(10.0)
    reclaimed = b.sweep()
    assert [l.id for l in reclaimed] == [lease.id]
    assert b.held()[0] == 0

    # После освобождения ёмкости бронь снова проходит.
    lease2 = b.acquire(req, ttl=10.0)
    assert lease2.id != lease.id


def test_ttl_sweep_is_triggered_by_acquire():
    """Подметание случается и на пути acquire() — протухшая бронь не должна
    блокировать новую, даже без явного sweep()."""
    clock = _FakeClock(0.0)
    ledger = LeaseLedger(clock=clock)
    b = ResourceBrain(max_ram_pressure=0.8, disk_reserve=1000, ledger=ledger)
    b.set_snapshot(ResourceSnapshot(1000, 1000, 100_000, 100_000))
    req = WorkloadRequest(estimated_ram=500)

    b.acquire(req, ttl=5.0)
    clock.advance(5.0)
    # Старая бронь протухла — acquire её подметёт и выдаст новую.
    lease2 = b.acquire(req, ttl=5.0)
    assert b.held()[0] == 500 and lease2 is not None


# --------------------------------------------------------------------------- #
# 4. Единая память: VRAM-претензия не задваивает пул.
# --------------------------------------------------------------------------- #

def test_unified_memory_does_not_double_count_vram():
    no_gpu = ResourceSnapshot(128, 100, 1000, 900)
    with_gpu = ResourceSnapshot(128, 100, 1000, 900, gpu_memory_used=64, gpu_memory_total=64)

    # Пул НИКОГДА не суммируется с VRAM: 128, а не 128+64.
    assert no_gpu.pool_total == 128
    assert with_gpu.pool_total == 128

    # VRAM — претензия к тому же пулу: доступное падает 100 → 64, а не растёт.
    assert no_gpu.unified_available == 100
    assert with_gpu.unified_available == 64

    # Admission отражает единый пул: заявка, влезающая при наивном учёте
    # (ram_available=100), отклоняется при честном едином учёте (avail=64).
    b = ResourceBrain(max_ram_pressure=0.8, disk_reserve=100)
    req = WorkloadRequest(estimated_ram=50, estimated_disk=0)
    assert b.admit(no_gpu, req).allowed              # 1-(50/128)=0.609 ≤ 0.8
    assert not b.admit(with_gpu, req).allowed        # 1-(14/128)=0.891 > 0.8


def test_lease_acquire_respects_unified_pool():
    # Бронь тоже считает VRAM-претензию: против 64 доступных заявка на 50 влезает,
    # а вторая на 50 — уже нет (единый пул, а не два независимых).
    snap = ResourceSnapshot(128, 100, 1000, 900, gpu_memory_used=64)
    b = ResourceBrain(max_ram_pressure=0.95, disk_reserve=100)
    b.set_snapshot(snap)
    b.acquire(WorkloadRequest(estimated_ram=50))
    with pytest.raises(errors.ResourceExhausted):
        b.acquire(WorkloadRequest(estimated_ram=50))


# --------------------------------------------------------------------------- #
# 5. Пороги PressureLevel и порядок rank_models.
# --------------------------------------------------------------------------- #

def test_pressure_level_thresholds():
    assert PressureLevel.from_pressure(0.0) is PressureLevel.NOMINAL
    assert PressureLevel.from_pressure(0.599) is PressureLevel.NOMINAL
    assert PressureLevel.from_pressure(0.60) is PressureLevel.ELEVATED
    assert PressureLevel.from_pressure(0.799) is PressureLevel.ELEVATED
    assert PressureLevel.from_pressure(0.80) is PressureLevel.HIGH
    assert PressureLevel.from_pressure(0.919) is PressureLevel.HIGH
    assert PressureLevel.from_pressure(0.92) is PressureLevel.CRITICAL
    assert PressureLevel.from_pressure(1.0) is PressureLevel.CRITICAL

    # Через снимок: pressure = 1 - avail/total.
    assert ResourceSnapshot(1000, 650, 1, 1).pressure_level is PressureLevel.NOMINAL   # 0.35
    assert ResourceSnapshot(1000, 350, 1, 1).pressure_level is PressureLevel.ELEVATED  # 0.65
    assert ResourceSnapshot(1000, 150, 1, 1).pressure_level is PressureLevel.HIGH      # 0.85
    assert ResourceSnapshot(1000, 50, 1, 1).pressure_level is PressureLevel.CRITICAL   # 0.95


def test_rank_models_orders_by_fit_health_resident():
    snap = ResourceSnapshot(1000, 500, 1, 1, model_resident=("a",))
    b = ResourceBrain()
    candidates = [
        {"id": "d", "health": "healthy", "ram_estimate": 1000, "latency_ms": 1},   # не влезает
        {"id": "c", "health": "unhealthy", "ram_estimate": 100, "latency_ms": 1},  # влезает, болен
        {"id": "b", "health": "healthy", "ram_estimate": 100, "latency_ms": 1},    # влезает, здоров
        {"id": "a", "health": "healthy", "ram_estimate": 100, "latency_ms": 1},    # влезает, здоров, резидент
    ]
    ranked = [m["id"] for m in b.rank_models(snap, candidates)]
    assert ranked == ["a", "b", "c", "d"]


def test_rank_models_uses_residency_state():
    snap = ResourceSnapshot(1000, 500, 1, 1)
    b = ResourceBrain(residency=ModelResidency(resident={"z"}))
    candidates = [
        {"id": "y", "health": "healthy", "ram_estimate": 100},
        {"id": "z", "health": "healthy", "ram_estimate": 100},  # резидент из brain.residency
    ]
    ranked = [m["id"] for m in b.rank_models(snap, candidates)]
    assert ranked[0] == "z"


# --------------------------------------------------------------------------- #
# 6. Жизненный цикл подсистемы: validate/start/stop + событие snapshot.
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_subsystem_lifecycle_emits_and_cancels():
    snap = ResourceSnapshot(1000, 300, 100_000, 90_000, probe="stub")  # pressure 0.7 → elevated
    brain = ResourceBrain()
    sub = ResourceBrainSubsystem(brain, _StubProbe(snap), interval=0.01)

    assert sub.name == "resource_brain" and sub.critical is False

    # validate: разовая проба ложится в brain.
    await sub.validate()
    assert brain.current_snapshot is snap

    q = events.subscribe()
    try:
        await sub.start()
        # Цикл эмитит первый снимок сразу; ждём немного.
        got = None
        for _ in range(50):
            await asyncio.sleep(0.01)
            while not q.empty():
                msg = json.loads(q.get_nowait())
                if msg.get("kind") == "resource.snapshot":
                    got = msg
                    break
            if got:
                break
        assert got is not None, "подсистема не эмитнула resource.snapshot"
        assert got["ram_total"] == 1000 and got["pressure_level"] == "elevated"
    finally:
        events.unsubscribe(q)
        await sub.stop()

    # stop снял фоновую задачу.
    assert sub._task is None
    # stop идемпотентна — повторный вызов не бросает.
    await sub.stop()


@pytest.mark.asyncio
async def test_subsystem_loop_survives_probe_error():
    """Ошибка пробы логируется, но цикл продолжается и не роняет процесс."""

    class _FlakyProbe:
        name = "flaky"
        calls = 0

        def available(self) -> bool:
            return True

        def snapshot(self, model_resident: tuple[str, ...] = ()) -> ResourceSnapshot:
            _FlakyProbe.calls += 1
            if _FlakyProbe.calls == 1:
                raise RuntimeError("transient probe failure")
            return ResourceSnapshot(1000, 800, 100_000, 90_000, probe="flaky")

    brain = ResourceBrain()
    sub = ResourceBrainSubsystem(brain, _FlakyProbe(), interval=0.01)
    await sub.start()
    try:
        # Дожидаемся, пока после первого сбоя цикл всё же снимет снимок.
        for _ in range(50):
            await asyncio.sleep(0.01)
            if brain.current_snapshot is not None:
                break
        assert brain.current_snapshot is not None
        assert not sub._task.done()  # цикл жив, не упал
    finally:
        await sub.stop()
