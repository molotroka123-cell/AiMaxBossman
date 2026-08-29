"""Stage 8 — адаптеры сильной изоляции: возможности заявляются честно, и на
хосте без runsc/KVM сильные режимы ОТВЕРГАЮТСЯ, а не деградируют молча."""
from __future__ import annotations

import pytest

from bossman import errors
from bossman.resource_brain import ResourceBrain, ResourceSnapshot
from bossman.sandbox import (
    PolicyMode,
    ResourceLeaseAdapter,
    SandboxManager,
    SandboxSpec,
)
from bossman.sandbox.models import IsolationTier
from bossman.sandbox.runtimes import GvisorRuntime, MicroVMRuntime
from bossman.sandbox.runtimes import strong as strong_mod


def _snap():
    return ResourceSnapshot(1_000_000_000, 800_000_000, 100_000_000_000, 80_000_000_000)


def _mgr(rt, tmp_path):
    brain = ResourceBrain(max_ram_pressure=0.95, disk_reserve=1000)
    return SandboxManager(rt, enabled=True, workspace_root=tmp_path,
                          resources=ResourceLeaseAdapter(brain=brain))


def test_capabilities_follow_real_host_support(monkeypatch):
    monkeypatch.setattr(strong_mod, "gvisor_available", lambda: False)
    assert GvisorRuntime().capabilities().tiers == frozenset()
    monkeypatch.setattr(strong_mod, "gvisor_available", lambda: True)
    caps = GvisorRuntime().capabilities()
    assert IsolationTier.CONTAINER in caps.tiers
    assert IsolationTier.MICROVM not in caps.tiers   # gVisor не даёт MicroVM


def test_microvm_requires_kvm_and_launcher(monkeypatch):
    monkeypatch.setattr(strong_mod, "kvm_available", lambda: False)
    assert MicroVMRuntime().capabilities().tiers == frozenset()
    monkeypatch.setattr(strong_mod, "kvm_available", lambda: True)
    monkeypatch.setattr(MicroVMRuntime, "_launcher_available", lambda self: True)
    assert IsolationTier.MICROVM in MicroVMRuntime().capabilities().tiers


@pytest.mark.asyncio
async def test_hostile_refused_without_kvm(tmp_path, monkeypatch):
    """Главный инвариант: HOSTILE без MicroVM = отказ, а не запуск в контейнере."""
    monkeypatch.setattr(strong_mod, "kvm_available", lambda: False)
    m = _mgr(MicroVMRuntime(workspace_root=tmp_path), tmp_path)
    with pytest.raises(errors.IsolationUnavailable):
        await m.create(SandboxSpec(task="untrusted", policy_mode=PolicyMode.HOSTILE),
                       snap=_snap())


@pytest.mark.asyncio
async def test_developer_refused_without_gvisor(tmp_path, monkeypatch):
    monkeypatch.setattr(strong_mod, "gvisor_available", lambda: False)
    m = _mgr(GvisorRuntime(workspace_root=tmp_path), tmp_path)
    with pytest.raises(errors.IsolationUnavailable):
        await m.create(SandboxSpec(task="t", policy_mode=PolicyMode.DEVELOPER), snap=_snap())


def test_offline_wrapper_disables_network_in_argv(monkeypatch):
    """Команда оборачивается изолятором с выключенной сетью, а не голым exec."""
    monkeypatch.setattr(strong_mod, "gvisor_available", lambda: True)
    rt = GvisorRuntime()
    s = SandboxSpec(task="t", labels={"argv": ["/bin/echo", "hi"]})
    from bossman.sandbox.models import SandboxSession
    sess = SandboxSession(id="sbx1", spec=s)
    argv = rt._argv(sess)
    assert argv[0] == "runsc" and "--network" in argv and "none" in argv
    assert argv[-2:] == ["/bin/echo", "hi"]
