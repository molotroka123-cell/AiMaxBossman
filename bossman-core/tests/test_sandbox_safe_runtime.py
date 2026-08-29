"""Stage 8 — тесты SAFE rootless-рантайма: реальное исполнение, изоляция
рабочей копии, лимиты, таймаут, отсутствие host-секретов в окружении, cleanup."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from bossman import errors
from bossman.resource_brain import ResourceBrain, ResourceSnapshot
from bossman.sandbox import (
    PolicyMode,
    ResourceLeaseAdapter,
    ResourceRequest,
    SandboxManager,
    SandboxSpec,
    SandboxState,
)
from bossman.sandbox.runtime import RuntimeTimeout
from bossman.sandbox.runtimes import SafeRuntime, safe_runtime_available
from bossman.sandbox.models import IsolationTier

pytestmark = pytest.mark.skipif(not safe_runtime_available(), reason="posix only")


def _snap():
    return ResourceSnapshot(1_000_000_000, 800_000_000, 100_000_000_000, 80_000_000_000)


def _mgr(tmp_path, **kw):
    rt = SafeRuntime(workspace_root=tmp_path)
    brain = ResourceBrain(max_ram_pressure=0.95, disk_reserve=1000)
    return SandboxManager(rt, enabled=True, workspace_root=tmp_path,
                          resources=ResourceLeaseAdapter(brain=brain), **kw), rt


def _spec(**kw):
    labels = kw.pop("labels", {})
    return SandboxSpec(
        task="t",
        resources=ResourceRequest(ram_bytes=256 * 1024 * 1024, disk_bytes=10 * 1024 * 1024,
                                  wall_time_seconds=kw.pop("wall", 20)),
        labels=labels, **kw)


def test_capabilities_are_honest():
    from bossman.sandbox import netguard
    caps = SafeRuntime().capabilities()
    # Заявляет ТОЛЬКО ROOTLESS — иначе политика пропустила бы HOSTILE через слабый рантайм.
    assert caps.tiers == frozenset({IsolationTier.ROOTLESS})
    # ALLOWLIST объявляется ровно тогда, когда барьер (root+nftables) реально
    # доступен: заявлять его без принуждения — значит выпустить процесс в сеть
    # мимо прокси. Обе стороны равенства берутся из факта, а не из константы.
    assert caps.supports_allowlist == netguard.available()


@pytest.mark.asyncio
async def test_hostile_policy_rejected_by_safe_runtime(tmp_path):
    m, _ = _mgr(tmp_path)
    with pytest.raises(errors.IsolationUnavailable):
        await m.create(_spec(policy_mode=PolicyMode.HOSTILE), snap=_snap())


@pytest.mark.asyncio
async def test_real_process_runs_and_completes(tmp_path):
    m, rt = _mgr(tmp_path)
    s = await m.create(_spec(labels={"argv": ["/bin/echo", "hello-bossman"]}), snap=_snap())
    await m.start(s)
    await m.poll(s)
    assert s.state == SandboxState.COMPLETED
    assert rt.exit_code(s) == 0
    out = (tmp_path / s.id / "out" / "stdout.log").read_text()
    assert "hello-bossman" in out


@pytest.mark.asyncio
async def test_nonzero_exit_marks_failed_and_releases(tmp_path):
    m, _ = _mgr(tmp_path)
    s = await m.create(_spec(labels={"argv": ["/bin/false"]}), snap=_snap())
    await m.start(s)
    await m.poll(s)
    assert s.state == SandboxState.DESTROYED   # FAILED → авто-снос
    assert not m.resources.active()


@pytest.mark.asyncio
async def test_wall_time_timeout(tmp_path):
    m, _ = _mgr(tmp_path)
    s = await m.create(_spec(wall=1, labels={"argv": ["/bin/sleep", "30"]}), snap=_snap())
    await m.start(s)
    await m.poll(s)   # менеджер ловит RuntimeTimeout и сносит
    assert s.state == SandboxState.DESTROYED
    assert not m.resources.active()


@pytest.mark.asyncio
async def test_workspace_is_a_copy_not_the_original(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "file.txt").write_text("original")
    (src / ".env").write_text("SECRET=leak-me")     # не должен копироваться
    root = tmp_path / "sbx"
    rt = SafeRuntime(workspace_root=root)
    brain = ResourceBrain(max_ram_pressure=0.95, disk_reserve=1000)
    m = SandboxManager(rt, enabled=True, workspace_root=root,
                       resources=ResourceLeaseAdapter(brain=brain))
    s = await m.create(_spec(workspace_source=str(src), trusted_source=True,
                             labels={"argv": ["/bin/echo", "ok"]}), snap=_snap())
    await m.start(s)
    await m.poll(s)
    work = root / s.id / "work"
    # копия есть, .env отфильтрован
    assert (work / "file.txt").read_text() == "original"
    assert not (work / ".env").exists()
    # правка копии не трогает оригинал
    (work / "file.txt").write_text("modified-in-sandbox")
    assert (src / "file.txt").read_text() == "original"


@pytest.mark.asyncio
async def test_untrusted_source_requires_stronger_isolation(tmp_path):
    """Дефолт — источник НЕДОВЕРЕННЫЙ: риск MEDIUM → нужен CONTAINER, а SAFE его
    не даёт → fail closed, без тихого исполнения в слабой изоляции."""
    src = tmp_path / "untrusted"
    src.mkdir()
    (src / "evil.py").write_text("print('hi')")
    m, _ = _mgr(tmp_path)
    with pytest.raises(errors.IsolationUnavailable):
        await m.create(_spec(workspace_source=str(src),
                             labels={"argv": ["/bin/echo", "x"]}), snap=_snap())


@pytest.mark.asyncio
async def test_env_carries_no_host_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-must-not-leak-into-sandbox")
    m, rt = _mgr(tmp_path)
    s = await m.create(_spec(labels={"argv": ["/usr/bin/env"]}), snap=_snap())
    await m.start(s)
    await m.poll(s)
    out = (tmp_path / s.id / "out" / "stdout.log").read_text()
    assert "sk-must-not-leak-into-sandbox" not in out
    assert "OPENROUTER_API_KEY" not in out
    assert "BOSSMAN_SANDBOX_ID" in out    # своё окружение на месте


@pytest.mark.asyncio
async def test_destroy_removes_workspace(tmp_path):
    m, _ = _mgr(tmp_path)
    s = await m.create(_spec(labels={"argv": ["/bin/echo", "x"]}), snap=_snap())
    await m.start(s)
    await m.poll(s)
    root = tmp_path / s.id
    assert root.exists()
    await m.destroy(s)
    assert not root.exists()
    assert not m.resources.active()


@pytest.mark.asyncio
async def test_argv_string_is_not_shell_interpreted(tmp_path):
    """Строка трактуется как ОДИН аргумент-исполняемый файл: инъекция через
    'echo hi; rm -rf /' не должна выполниться шеллом."""
    m, _ = _mgr(tmp_path)
    marker = tmp_path / "pwned.txt"
    s = await m.create(_spec(labels={"argv": f"/bin/echo hi; touch {marker}"}), snap=_snap())
    await m.start(s)
    await m.poll(s)
    assert not marker.exists()          # шелл не выполнял строку
    assert s.state == SandboxState.DESTROYED   # spawn упал → FAILED → снос


@pytest.mark.asyncio
async def test_offline_requires_runtime_enforcement(tmp_path, monkeypatch):
    """OFFLINE обязан энфорситься рантаймом. Если netns/unshare нет — «OFFLINE»
    был бы фикцией (процесс с полной сетью), поэтому fail closed, а не запуск."""
    import bossman.sandbox.runtimes.safe as safe_mod
    monkeypatch.setattr(safe_mod, "_unshare_available", lambda: False)
    m, rt = _mgr(tmp_path)
    assert rt.capabilities().supports_offline is False
    with pytest.raises(errors.IsolationUnavailable):
        await m.create(_spec(labels={"argv": ["/bin/echo", "x"]}), snap=_snap())
