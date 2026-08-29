"""Stage 8 — принудительный барьер egress: прямой сокет мимо прокси невозможен.

Переменные http_proxy — просьба; вредоносный код их игнорирует. Здесь проверяем
барьер, который игнорировать нельзя: выделенный uid + nftables.
"""
from __future__ import annotations

import asyncio
import os
import socket
import textwrap
from pathlib import Path

import pytest

from bossman.resource_brain import ResourceBrain, ResourceSnapshot
from bossman.sandbox import (
    NetworkMode,
    PolicyMode,
    ResourceLeaseAdapter,
    ResourceRequest,
    SandboxManager,
    SandboxSpec,
)
from bossman.sandbox import netguard
from bossman.sandbox.models import IsolationTier, RuntimeCapabilities
from bossman.sandbox.runtimes import SafeRuntime

pytestmark = pytest.mark.skipif(
    not netguard.available(),
    reason="нужен root + nftables (барьер иначе честно объявляется недоступным)")


def _snap():
    return ResourceSnapshot(1_000_000_000, 800_000_000, 100_000_000_000, 80_000_000_000)


def _free_listener():
    """Слушатель, к которому песочница НЕ должна дотянуться напрямую."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(5)
    return s, s.getsockname()[1]


PROBE = textwrap.dedent("""
    import socket, sys
    s = socket.socket(); s.settimeout(3)
    try:
        s.connect(("127.0.0.1", int(sys.argv[1])))
        print("CONNECTED")
    except Exception as exc:
        print("BLOCKED:", type(exc).__name__)
""")


class _ContainerRuntime(SafeRuntime):
    """Дубль для проверки САМОГО барьера egress.

    ALLOWLIST поднимает риск до MEDIUM, а тот требует CONTAINER — обычный
    SafeRuntime (ROOTLESS) политика отвергнет, и это правильно: модель риска
    ослаблять нельзя. Реальные CONTAINER/MICROVM-рантаймы (GvisorRuntime,
    MicroVMRuntime) НАСЛЕДУЮТ SafeRuntime, а значит и весь механизм блокировки,
    который здесь и проверяется.
    """

    name = "safe-container-double"

    def capabilities(self) -> RuntimeCapabilities:
        base = super().capabilities()
        return RuntimeCapabilities(
            name=self.name,
            tiers=frozenset({IsolationTier.ROOTLESS, IsolationTier.CONTAINER}),
            supports_offline=base.supports_offline,
            supports_allowlist=base.supports_allowlist,   # честно: как у барьера
            supports_readonly_root=base.supports_readonly_root,
            supports_seccomp=base.supports_seccomp,
            supports_pid_limit=True, supports_mem_limit=True,
        )


def test_strong_runtimes_inherit_the_barrier():
    """Gvisor/MicroVM расширяют SafeRuntime, поэтому получают тот же барьер."""
    from bossman.sandbox.runtimes import GvisorRuntime, MicroVMRuntime
    for cls in (GvisorRuntime, MicroVMRuntime):
        assert issubclass(cls, SafeRuntime)
        assert hasattr(cls, "_apply_lockdown") and hasattr(cls, "_release_lockdown")


def test_safe_runtime_alone_still_refuses_allowlist(tmp_path):
    """Барьер НЕ ослабляет модель риска: ROOTLESS-рантайм всё равно не берёт
    ALLOWLIST, потому что тот требует CONTAINER."""
    from bossman import errors
    rt = SafeRuntime(workspace_root=tmp_path)
    assert rt.capabilities().supports_allowlist is True   # барьер есть...
    brain = ResourceBrain(max_ram_pressure=0.95, disk_reserve=1000)
    m = SandboxManager(rt, enabled=True, workspace_root=tmp_path,
                       resources=ResourceLeaseAdapter(brain=brain))

    async def _go():
        with pytest.raises(errors.IsolationUnavailable):   # ...но tier не тот
            await m.create(SandboxSpec(task="t", policy_mode=PolicyMode.SAFE,
                                       network_mode=NetworkMode.ALLOWLIST,
                                       allowlist=("api.github.com",)), snap=_snap())
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_go())


def _make_traversable(path):
    """pytest создаёт tmp_path под 0700 root — процесс под выделенным uid туда
    не войдёт. Открываем проход по всей цепочке (только на чтение/выполнение)."""
    p = Path(path)
    for parent in [p, *p.parents]:
        try:
            os.chmod(parent, os.stat(parent).st_mode | 0o055)
        except (OSError, PermissionError):
            break


def _mgr(tmp_path):
    _make_traversable(tmp_path)
    rt = _ContainerRuntime(workspace_root=tmp_path)
    assert rt.capabilities().supports_allowlist is True
    brain = ResourceBrain(max_ram_pressure=0.95, disk_reserve=1000)
    return SandboxManager(rt, enabled=True, workspace_root=tmp_path,
                          resources=ResourceLeaseAdapter(brain=brain)), rt


def test_uid_is_deterministic_and_outside_system_range():
    uid = netguard.sandbox_uid("sbx_abc123")
    assert uid == netguard.sandbox_uid("sbx_abc123")
    assert netguard.UID_BASE <= uid < netguard.UID_BASE + netguard.UID_RANGE
    assert uid > 1000            # не системный и не реальный пользователь


def test_lockdown_rules_allow_only_proxy(tmp_path):
    lock = netguard.EgressLockdown("sbx_rules_demo")
    try:
        lock.apply("127.0.0.1", 8899)
        rules = lock.rules()
        assert f"skuid {lock.uid}" in rules
        assert "dport 8899 accept" in rules
        assert "reject" in rules          # всё остальное — отказ
    finally:
        lock.remove()
    assert "bossman_sbx" not in netguard._nft("list", "tables", check=False).stdout or \
        lock.table not in netguard._nft("list", "tables", check=False).stdout


@pytest.mark.asyncio
async def test_direct_socket_blocked_in_allowlist_sandbox(tmp_path):
    """Главное: процесс в ALLOWLIST-песочнице НЕ может открыть прямой сокет."""
    forbidden_srv, forbidden_port = _free_listener()
    probe = tmp_path / "probe.py"
    probe.write_text(PROBE)
    os.chmod(probe, 0o644)

    m, rt = _mgr(tmp_path)
    s = await m.create(SandboxSpec(
        task="probe", policy_mode=PolicyMode.CONNECTED,
        network_mode=NetworkMode.ALLOWLIST, allowlist=("api.github.com",),
        resources=ResourceRequest(wall_time_seconds=20),
        labels={"argv": ["python3", str(probe), str(forbidden_port)]}), snap=_snap())
    try:
        await m.start(s)
        assert s.spec.labels.get("egress_proxy")     # прокси поднят менеджером
        await m.poll(s)
        out = (tmp_path / s.id / "out" / "stdout.log").read_text()
        assert "BLOCKED" in out, f"прямой сокет НЕ заблокирован: {out!r}"
        assert "CONNECTED" not in out
    finally:
        await m.destroy(s)
        forbidden_srv.close()


@pytest.mark.asyncio
async def test_lockdown_removed_with_sandbox(tmp_path):
    """Правила firewall не остаются в хосте после сноса песочницы."""
    m, rt = _mgr(tmp_path)
    s = await m.create(SandboxSpec(
        task="t", policy_mode=PolicyMode.CONNECTED, network_mode=NetworkMode.ALLOWLIST,
        allowlist=("api.github.com",), resources=ResourceRequest(wall_time_seconds=10),
        labels={"argv": ["/bin/echo", "x"]}), snap=_snap())
    await m.start(s)
    table = netguard._table_name(s.id)
    assert table in netguard._nft("list", "tables", check=False).stdout
    await m.poll(s)
    await m.destroy(s)
    assert table not in netguard._nft("list", "tables", check=False).stdout


@pytest.mark.asyncio
async def test_process_runs_under_dedicated_uid(tmp_path):
    """Процесс не остаётся под uid ядра — иначе skuid-правило его не поймает."""
    probe = tmp_path / "whoami.py"
    probe.write_text("import os; print('UID=%d' % os.getuid())")
    os.chmod(probe, 0o644)
    m, rt = _mgr(tmp_path)
    s = await m.create(SandboxSpec(
        task="t", policy_mode=PolicyMode.CONNECTED, network_mode=NetworkMode.ALLOWLIST,
        allowlist=("api.github.com",), resources=ResourceRequest(wall_time_seconds=20),
        labels={"argv": ["python3", str(probe)]}), snap=_snap())
    try:
        await m.start(s)
        await m.poll(s)
        out = (tmp_path / s.id / "out" / "stdout.log").read_text()
        assert f"UID={netguard.sandbox_uid(s.id)}" in out, out
        assert "UID=0" not in out            # не root
    finally:
        await m.destroy(s)
