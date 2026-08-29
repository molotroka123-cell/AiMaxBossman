"""Stage 8 — реальный egress-барьер для ALLOWLIST: прокси спрашивает NetworkGuard
на каждом CONNECT, по умолчанию отказывает и пишет решения в траекторию."""
from __future__ import annotations

import asyncio
import os

import pytest

from bossman.sandbox import EgressProxy
from bossman.sandbox.runtimes import safe_runtime_available
from bossman.sandbox.netguard import available as egress_lockdown_available
from bossman.sandbox.egress import parse_connect
from bossman.sandbox.models import IsolationTier, NetworkMode, PolicyMode, SandboxPolicy
from bossman.sandbox.trajectory import TrajectoryRecorder


def _policy(net=NetworkMode.ALLOWLIST, allowlist=("api.github.com",)):
    return SandboxPolicy(mode=PolicyMode.CONNECTED, network_mode=net,
                         isolation_tier=IsolationTier.CONTAINER, allowlist=tuple(allowlist),
                         read_only_root=True, drop_caps=True, no_new_privs=True)


def test_parse_connect_only_accepts_tunnel():
    assert parse_connect(b"CONNECT api.github.com:443 HTTP/1.1") == ("api.github.com", 443)
    assert parse_connect(b"CONNECT [::1]:443 HTTP/1.1") == ("::1", 443)
    # обычный GET не туннель — прокси не должен становиться открытым релеем
    assert parse_connect(b"GET http://evil.example/ HTTP/1.1") is None
    assert parse_connect(b"CONNECT nonsense HTTP/1.1") is None


@pytest.mark.asyncio
async def test_proxy_refuses_to_run_offline():
    p = EgressProxy(_policy(net=NetworkMode.OFFLINE))
    with pytest.raises(RuntimeError):
        await p.start()          # в OFFLINE выхода нет by design


async def _connect_via(proxy: EgressProxy, target: str) -> bytes:
    host, port = proxy.address
    r, w = await asyncio.open_connection(host, port)
    w.write(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    await w.drain()
    data = await asyncio.wait_for(r.readline(), timeout=5)
    w.close()
    return data


@pytest.mark.asyncio
async def test_non_allowlisted_host_denied():
    rec = TrajectoryRecorder("sbx1")
    p = EgressProxy(_policy(), recorder=rec)
    await p.start()
    try:
        resp = await _connect_via(p, "evil.example:443")
    finally:
        await p.stop()
    assert b"403" in resp
    assert p.decisions[-1].allowed is False
    # решение попало в траекторию
    assert any(e["kind"] == "network" and e["allowed"] is False for e in rec.events)


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [
    "127.0.0.1:22",              # loopback
    "169.254.169.254:80",        # cloud metadata
    "10.0.0.5:5432",             # приватная сеть
    "postgres:5432",             # control-plane по имени
])
async def test_protected_targets_denied_even_when_allowlisted(target):
    host = target.rsplit(":", 1)[0]
    # даже если хост ЯВНО в allowlist — защищённые сети всё равно запрещены
    p = EgressProxy(_policy(allowlist=(host,)))
    await p.start()
    try:
        resp = await _connect_via(p, target)
    finally:
        await p.stop()
    assert b"403" in resp, target


@pytest.mark.asyncio
async def test_allowlisted_host_is_tunneled():
    """Разрешённый хост действительно проксируется до реального сокета."""
    async def echo(reader, writer):
        data = await reader.read(16)
        writer.write(b"upstream:" + data)
        await writer.drain()
        writer.close()

    upstream = await asyncio.start_server(echo, "127.0.0.1", 0)
    up_host, up_port = upstream.sockets[0].getsockname()[:2]
    # allowlist по литеральному IP запрещён (приватная сеть), поэтому проверяем
    # разрешающий путь через подменённый guard, оставляя всю остальную механику.
    class _AllowGuard:
        def decide(self, host, policy, port=None):
            from bossman.sandbox.network import NetDecision
            return NetDecision(True, "test-allow", host, port)

    p = EgressProxy(_policy(allowlist=(up_host,)), guard=_AllowGuard())
    await p.start()
    try:
        host, port = p.address
        r, w = await asyncio.open_connection(host, port)
        w.write(f"CONNECT {up_host}:{up_port} HTTP/1.1\r\n\r\n".encode())
        await w.drain()
        status = await asyncio.wait_for(r.readline(), timeout=5)
        assert b"200" in status
        await r.readline()                    # пустая строка после заголовка
        w.write(b"ping")
        await w.drain()
        body = await asyncio.wait_for(r.read(32), timeout=5)
        assert b"upstream:ping" in body
        w.close()
    finally:
        await p.stop()
        upstream.close()
        await upstream.wait_closed()


# ---------- интеграция с менеджером ----------

def _mgr(tmp_path):
    from bossman.resource_brain import ResourceBrain, ResourceSnapshot
    from bossman.sandbox import ResourceLeaseAdapter, SandboxManager
    from bossman.sandbox.runtimes import SafeRuntime
    brain = ResourceBrain(max_ram_pressure=0.95, disk_reserve=1000)
    rt = SafeRuntime(workspace_root=tmp_path)
    m = SandboxManager(rt, enabled=True, workspace_root=tmp_path,
                       resources=ResourceLeaseAdapter(brain=brain))
    return m, ResourceSnapshot(1_000_000_000, 800_000_000, 100_000_000_000, 80_000_000_000)


@pytest.mark.asyncio
@pytest.mark.skipif(not safe_runtime_available(),
                    reason="SAFE реальное исполнение недоступно в этом окружении "
                           "(нет обхода родительских каталогов сброшенным uid)")
async def test_offline_sandbox_starts_no_proxy(tmp_path):
    from bossman.sandbox import ResourceRequest, SandboxSpec
    m, snap = _mgr(tmp_path)
    s = await m.create(SandboxSpec(task="t", resources=ResourceRequest(wall_time_seconds=10),
                                   labels={"argv": ["/bin/echo", "x"]}), snap=snap)
    await m.start(s)
    await m.poll(s)
    assert m.proxies == {}          # в OFFLINE выхода нет вовсе
    await m.destroy(s)


@pytest.mark.asyncio
@pytest.mark.skipif(not (safe_runtime_available() and egress_lockdown_available()),
                    reason="нужен рабочий SAFE-рантайм И egress-барьер "
                           "(root + nftables) — на CI-раннере их нет")
async def test_allowlist_sandbox_gets_proxy_and_releases_it(tmp_path):
    """ALLOWLIST поднимает прокси и отдаёт его адрес процессу; destroy закрывает."""
    from bossman.sandbox import NetworkMode, PolicyMode, ResourceRequest, SandboxSpec
    from bossman.sandbox.models import IsolationTier, RuntimeCapabilities
    m, snap = _mgr(tmp_path)
    # SafeRuntime не умеет allowlist — подменяем возможности, механику оставляем.
    m.runtime.capabilities = lambda: RuntimeCapabilities(
        name="safe", tiers=frozenset({IsolationTier.ROOTLESS, IsolationTier.CONTAINER}),
        supports_offline=True, supports_allowlist=True)
    s = await m.create(SandboxSpec(task="t", policy_mode=PolicyMode.CONNECTED,
                                   network_mode=NetworkMode.ALLOWLIST,
                                   allowlist=("api.github.com",),
                                   resources=ResourceRequest(wall_time_seconds=10),
                                   labels={"argv": ["/bin/echo", "x"]}), snap=snap)
    await m.start(s)
    assert s.id in m.proxies
    assert s.spec.labels.get("egress_proxy", "").startswith("127.0.0.1:")
    await m.poll(s)
    await m.destroy(s)
    assert m.proxies == {}          # выход закрыт вместе с песочницей


@pytest.mark.asyncio
async def test_proxy_env_is_injected_into_sandbox_process(tmp_path):
    """Процесс песочницы обязан ходить через прокси: адрес приходит стандартными
    переменными, а NO_PROXY пуст — исключений мимо барьера нет."""
    from bossman.sandbox.runtimes import SafeRuntime
    from bossman.sandbox.models import SandboxSession, SandboxSpec
    rt = SafeRuntime(workspace_root=tmp_path)
    s = SandboxSession(id="sbx1", spec=SandboxSpec(task="t",
                       labels={"egress_proxy": "127.0.0.1:9999"}))
    env = rt._env(s)
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy"):
        assert env[k] == "http://127.0.0.1:9999"
    assert env["no_proxy"] == "" and env["NO_PROXY"] == ""


@pytest.mark.asyncio
async def test_no_proxy_env_without_egress(tmp_path):
    """В OFFLINE прокси нет — и переменных прокси в окружении тоже быть не должно."""
    from bossman.sandbox.runtimes import SafeRuntime
    from bossman.sandbox.models import SandboxSession, SandboxSpec
    rt = SafeRuntime(workspace_root=tmp_path)
    s = SandboxSession(id="sbx2", spec=SandboxSpec(task="t"))
    env = rt._env(s)
    assert not any("proxy" in k.lower() for k in env)
