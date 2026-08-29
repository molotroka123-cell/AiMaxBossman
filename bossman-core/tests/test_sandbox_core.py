"""Stage 8 — тесты ядра песочницы: автомат, рантайм, менеджер, аренды, recovery."""
from __future__ import annotations

import pytest

from bossman import errors
from bossman.resource_brain import ResourceBrain, ResourceSnapshot
from bossman.sandbox import (
    FakeRuntime,
    NetworkMode,
    PolicyMode,
    ResourceLeaseAdapter,
    ResourceRequest,
    RiskLevel,
    SandboxManager,
    SandboxSpec,
    SandboxState,
    allowed_transitions,
    can_transition,
)
from bossman.sandbox.models import IsolationTier


def _snap():
    # маленькие числа в стиле приёмочного теста Resource Brain
    return ResourceSnapshot(1000, 500, 10000, 8000)


def _mgr(enabled=True, tmp="/tmp/claude-sbx", runtime=None):
    brain = ResourceBrain(max_ram_pressure=0.8, disk_reserve=1000)
    adapter = ResourceLeaseAdapter(brain=brain)
    return SandboxManager(runtime or FakeRuntime(), enabled=enabled,
                          workspace_root=tmp, resources=adapter)


def _spec(ram=100, disk=10, **kw):
    return SandboxSpec(task="t", resources=ResourceRequest(ram_bytes=ram, disk_bytes=disk), **kw)


# ---------- автомат ----------

def test_valid_transition_graph():
    assert can_transition(SandboxState.REQUESTED, SandboxState.ADMITTED)
    assert can_transition(SandboxState.RUNNING, SandboxState.COMPLETED)
    assert SandboxState.DESTROYING in allowed_transitions(SandboxState.FAILED)


def test_invalid_transitions_blocked():
    assert not can_transition(SandboxState.REQUESTED, SandboxState.RUNNING)
    assert not can_transition(SandboxState.COMPLETED, SandboxState.RUNNING)
    assert not can_transition(SandboxState.DESTROYED, SandboxState.REQUESTED)
    assert allowed_transitions(SandboxState.DESTROYED) == frozenset()


@pytest.mark.asyncio
async def test_manager_rejects_illegal_transition(tmp_path):
    m = _mgr(tmp=tmp_path)
    s = await m.create(_spec(), snap=_snap())
    # ADMITTED -> RUNNING напрямую запрещено (нужно PREPARING/READY)
    with pytest.raises(errors.InvalidTransition):
        m._transition(s, SandboxState.RUNNING, "illegal")


# ---------- OFF = OFF ----------

@pytest.mark.asyncio
async def test_sandbox_disabled_creates_nothing(tmp_path):
    m = _mgr(enabled=False, tmp=tmp_path)
    with pytest.raises(errors.SandboxDisabled):
        await m.create(_spec(), snap=_snap())
    assert not m.sessions


# ---------- happy path ----------

@pytest.mark.asyncio
async def test_full_lifecycle_success(tmp_path):
    m = _mgr(tmp=tmp_path)
    s = await m.create(_spec(), snap=_snap())
    assert s.state == SandboxState.ADMITTED and s.lease_id
    await m.start(s)
    assert s.state == SandboxState.RUNNING
    await m.poll(s)
    assert s.state == SandboxState.COMPLETED
    await m.destroy(s)
    assert s.state == SandboxState.DESTROYED
    assert not m.resources.active()  # аренда освобождена


# ---------- ресурсы ----------

@pytest.mark.asyncio
async def test_resource_deny_fails_closed(tmp_path):
    m = _mgr(tmp=tmp_path)
    # ram 400 против snapshot(avail=500,total=1000) при max_pressure 0.8 → отказ
    with pytest.raises(errors.ResourceExhausted):
        await m.create(_spec(ram=400), snap=_snap())
    s = list(m.sessions.values())[-1]
    assert s.state == SandboxState.FAILED
    assert not m.resources.active()


@pytest.mark.asyncio
async def test_resource_release_and_double_release(tmp_path):
    m = _mgr(tmp=tmp_path)
    s = await m.create(_spec(), snap=_snap())
    assert m.resources.release(s) is True
    # повторный release безопасен и не бросает
    assert m.resources.release(s) is False
    assert not m.resources.active()


# ---------- риск-эскалация / fail closed ----------

@pytest.mark.asyncio
async def test_risk_escalation_requires_microvm(tmp_path):
    # HOSTILE-политика → риск HOSTILE → нужен MICROVM; рантайм только ROOTLESS → отказ
    weak = FakeRuntime(tier=IsolationTier.ROOTLESS)
    m = _mgr(tmp=tmp_path, runtime=weak)
    with pytest.raises(errors.IsolationUnavailable):
        await m.create(_spec(policy_mode=PolicyMode.HOSTILE), snap=_snap())


# ---------- сбои рантайма ----------

@pytest.mark.asyncio
async def test_runtime_crash_fails_and_releases(tmp_path):
    m = _mgr(tmp=tmp_path)
    s = await m.create(_spec(labels={"fake_scenario": "crash"}), snap=_snap())
    with pytest.raises(Exception):
        await m.start(s)
    assert s.state == SandboxState.DESTROYED   # provал → снос
    assert not m.resources.active()


@pytest.mark.asyncio
async def test_runtime_timeout_on_poll(tmp_path):
    m = _mgr(tmp=tmp_path)
    s = await m.create(_spec(labels={"fake_scenario": "timeout"}), snap=_snap())
    await m.start(s)
    await m.poll(s)
    assert s.state == SandboxState.DESTROYED
    assert not m.resources.active()


@pytest.mark.asyncio
async def test_destroy_failure_still_releases_lease(tmp_path):
    m = _mgr(tmp=tmp_path)
    s = await m.create(_spec(labels={"fake_scenario": "destroy_failure"}), snap=_snap())
    await m.start(s)
    await m.poll(s)  # COMPLETED
    await m.destroy(s)
    # снос рантайма упал, но песочница считается снесённой и аренда освобождена
    assert s.state == SandboxState.DESTROYED
    assert not m.resources.active()


# ---------- восстановление после рестарта ----------

@pytest.mark.asyncio
async def test_restart_recovery_reclaims_leases(tmp_path):
    m = _mgr(tmp=tmp_path)
    s1 = await m.create(_spec(), snap=_snap())
    await m.start(s1)
    assert m.resources.active()
    recovered = await m.recover()
    assert s1.id in recovered
    assert s1.state == SandboxState.DESTROYED
    assert not m.resources.active()
