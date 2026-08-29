"""Stage 9 — Sandbox SAFE runtime E2E: create → start → run → collect → destroy.

На POSIX — живой прогон SAFE-рантайма и негативные сценарии.
На Windows SAFE-исполнение процессов недоступно по дизайну (posix-only rlimits
и unshare): проверяется честный FAIL-CLOSED, живая часть помечается SKIP.
"""
import os

import pytest

# Менеджер создаётся тестом напрямую (enabled=True) — глобальный env-флаг
# BOSSMAN_SANDBOX_ENABLED не нужен: здесь проверяется сам рантайм и fail-closed.

posix_only = pytest.mark.skipif(os.name != "posix",
                                reason="SAFE-исполнение процессов POSIX-only "
                                       "(rlimits/unshare); на Windows — fail-closed")


def _mgr(tmp_path):
    from bossman.resource_brain import ResourceBrain, ResourceSnapshot
    from bossman.sandbox import ResourceLeaseAdapter, SandboxManager
    from bossman.sandbox.runtimes import SafeRuntime
    brain = ResourceBrain(max_ram_pressure=0.95, disk_reserve=1000)
    rt = SafeRuntime(workspace_root=tmp_path)
    m = SandboxManager(rt, enabled=True, workspace_root=tmp_path,
                       resources=ResourceLeaseAdapter(brain=brain))
    snap = ResourceSnapshot(1_000_000_000, 800_000_000, 100_000_000_000, 80_000_000_000)
    return m, snap


# ---------- FAIL-CLOSED (проверяется на любой ОС) ----------

async def test_stage9_offline_without_enforceable_isolation_fails_closed(tmp_path):
    """OFFLINE требует реального сетевого барьера; нет unshare → IsolationUnavailable."""
    from bossman.sandbox import SandboxSpec, ResourceRequest
    from bossman.sandbox.models import NetworkMode
    from bossman import errors
    if os.name == "posix":
        import shutil
        if shutil.which("unshare"):
            pytest.skip("unshare доступен — OFFLINE исполняем, негатив неприменим")
    m, snap = _mgr(tmp_path)
    with pytest.raises(errors.IsolationUnavailable):
        await m.create(SandboxSpec(task="t", network_mode=NetworkMode.OFFLINE,
                                   resources=ResourceRequest(wall_time_seconds=5),
                                   labels={"argv": ["/bin/echo", "x"]}), snap=snap)


async def test_stage9_hostile_rejected_by_safe_runtime(tmp_path):
    """HOSTILE требует сильной изоляции; SAFE её не заявляет → отказ."""
    from bossman.sandbox import SandboxSpec, ResourceRequest, PolicyMode
    from bossman import errors
    m, snap = _mgr(tmp_path)
    with pytest.raises(errors.IsolationUnavailable):
        await m.create(SandboxSpec(task="t", policy_mode=PolicyMode.HOSTILE,
                                   resources=ResourceRequest(wall_time_seconds=5),
                                   labels={"argv": ["/bin/echo", "x"]}), snap=snap)


async def test_stage9_disabled_sandbox_activates_nothing(tmp_path, monkeypatch):
    """OFF=OFF: при выключенной фиче подсистема не активирует рантайм."""
    from bossman.sandbox import subsystem
    monkeypatch.delenv("BOSSMAN_SANDBOX_ENABLED", raising=False)
    ss = subsystem.SandboxSubsystem()
    await ss.start()          # OFF → start() ничего не поднимает, без ошибок
    try:
        assert not subsystem.sandbox_enabled()
        assert not ss.manager.enabled
    finally:
        await ss.stop()


# ---------- LIVE E2E (только POSIX) ----------

@posix_only
async def test_stage9_sandbox_full_lifecycle(tmp_path):
    """create → start → run argv → collect artifact → destroy; destroy идемпотентен."""
    from bossman.sandbox import SandboxSpec, ResourceRequest
    m, snap = _mgr(tmp_path)
    src = tmp_path / "src"; src.mkdir()
    (src / "out.txt").write_text("stage9-artifact", encoding="utf-8")

    s = await m.create(SandboxSpec(task="t", resources=ResourceRequest(wall_time_seconds=15),
                                   workspace_source=str(src),
                                   labels={"argv": ["/bin/cat", "out.txt"]}), snap=snap)
    await m.start(s)
    for _ in range(100):
        await m.poll(s)
        if s.exit_code is not None or s.state.value in ("DONE", "FINISHED", "SUCCEEDED", "COMPLETED"):
            break
    assert s.exit_code == 0, getattr(s, "error", None)
    # артефакт проходит ArtifactGate
    art = m.artifact_gate(s).inspect("out.txt")
    assert art is not None
    await m.destroy(s)
    # идемпотентность: повторный destroy не бросает
    await m.destroy(s)


@posix_only
async def test_stage9_sandbox_timeout_kills_process(tmp_path):
    """wall-time превышен → RuntimeTimeout, состояние честное."""
    from bossman.sandbox import SandboxSpec, ResourceRequest
    from bossman.sandbox.runtime import RuntimeTimeout
    m, snap = _mgr(tmp_path)
    s = await m.create(SandboxSpec(task="t", resources=ResourceRequest(wall_time_seconds=1),
                                   labels={"argv": ["/bin/sleep", "30"]}), snap=snap)
    await m.start(s)
    outcome: Exception | None = None
    for _ in range(300):
        try:
            state = await m.poll(s)
        except RuntimeTimeout as exc:
            outcome = exc
            break
        if s.exit_code is not None:
            break
    assert outcome is not None or s.exit_code not in (0, None)
    await m.destroy(s)


@posix_only
async def test_stage9_sandbox_traversal_and_executable_quarantined(tmp_path):
    """collect: traversal-путь отвергнут; исполняемый артефакт — карантин."""
    from bossman.sandbox import SandboxSpec, ResourceRequest
    from bossman import errors
    m, snap = _mgr(tmp_path)
    s = await m.create(SandboxSpec(task="t", resources=ResourceRequest(wall_time_seconds=10),
                                   labels={"argv": ["/bin/echo", "x"]}), snap=snap)
    gate = m.artifact_gate(s)
    with pytest.raises(errors.ArtifactRejected):
        gate.inspect("../etc/passwd")
    (tmp_path / s.id / "tool.bin").write_bytes(b"MZ\x00\x00")
    art = gate.inspect("tool.bin")
    assert art.quarantined
    await m.destroy(s)
