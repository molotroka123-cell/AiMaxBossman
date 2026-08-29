"""Stage 8 — детерминированный сетевой барьер (control-plane решение).

По умолчанию block. Даже в ALLOWLIST/INTERNET режимах НИКОГДА не пускаем в
localhost, приватные и link-local сети (включая cloud-metadata 169.254.169.254)
и в control-plane Bossman. Это защита от SSRF/exfiltration изнутри песочницы.
Решение принимает control plane — сама песочница не может его переопределить.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from .models import NetworkMode, SandboxPolicy

# Хосты, которые всегда запрещены как имена (до/без резолвинга).
_BLOCKED_HOSTNAMES = frozenset({
    "localhost", "localhost.localdomain", "ip6-localhost",
    "metadata", "metadata.google.internal",
    # control-plane Bossman по умолчанию (docker-сеть):
    "postgres", "redis", "litellm", "llama-swap", "bossman-core", "gateway",
})


@dataclass(slots=True)
class NetDecision:
    allowed: bool
    reason: str
    host: str
    port: int | None = None


def _is_protected_ip(ip: ipaddress._BaseAddress) -> str | None:
    """Вернуть причину защиты, если IP нельзя выпускать наружу, иначе None."""
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:      # 169.254/16, fe80::/10 — включает cloud metadata
        return "link_local/metadata"
    if ip.is_private:         # 10/8, 172.16/12, 192.168/16, fc00::/7
        return "private_lan"
    if ip.is_multicast:
        return "multicast"
    if ip.is_reserved or ip.is_unspecified:
        return "reserved"
    return None


class NetworkGuard:
    """Единая точка решения egress. Fail closed: неизвестное → block."""

    def __init__(self, extra_blocked_hosts: frozenset[str] = frozenset()) -> None:
        self.blocked_hosts = _BLOCKED_HOSTNAMES | {h.lower() for h in extra_blocked_hosts}

    def decide(self, host: str, policy: SandboxPolicy, port: int | None = None) -> NetDecision:
        host_l = (host or "").strip().lower().rstrip(".")
        if not host_l:
            return NetDecision(False, "empty host", host, port)

        # 1) OFFLINE — блок всего, безусловно.
        if policy.network_mode == NetworkMode.OFFLINE:
            return NetDecision(False, "network OFFLINE", host_l, port)

        # 2) Защищённые имена — всегда блок, в любом режиме.
        if host_l in self.blocked_hosts:
            return NetDecision(False, f"blocked control-plane/host '{host_l}'", host_l, port)

        # 3) Литеральные IP — классифицируем и защищаем приватное/metadata.
        ip = _parse_ip(host_l)
        if ip is not None:
            why = _is_protected_ip(ip)
            if why:
                return NetDecision(False, f"protected network ({why})", host_l, port)

        # 4) ALLOWLIST — только явные хосты (и не защищённые, что уже проверено).
        if policy.network_mode == NetworkMode.ALLOWLIST:
            if _host_in_allowlist(host_l, policy.allowlist):
                return NetDecision(True, "allowlisted", host_l, port)
            return NetDecision(False, "not in allowlist", host_l, port)

        # 5) INTERNET — публичные адреса разрешены (приватные уже отсечены выше).
        if policy.network_mode == NetworkMode.INTERNET:
            return NetDecision(True, "internet egress", host_l, port)

        return NetDecision(False, "default deny", host_l, port)


def _parse_ip(host: str):
    try:
        # host может быть в скобках для ipv6
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def _host_in_allowlist(host: str, allowlist: tuple[str, ...]) -> bool:
    for entry in allowlist:
        e = entry.strip().lower().rstrip(".")
        if not e:
            continue
        if host == e or host.endswith("." + e):
            return True
    return False
