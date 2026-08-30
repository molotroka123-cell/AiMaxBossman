"""Общие security-примитивы для plugin-адаптеров (V1).

Закрывают конкретные слабые места, найденные аудитом bundle
(SKILLS_PLUGINS_FINAL_BUG_CHECK.md):

* F1 — SSRF: валидируем не только литеральный IP, но и РЕЗОЛВ имени, и
  запрещаем небезопасный redirect (каждый hop перепроверяется, а не «проверили
  один раз и доверяем Location»).
* F2 — DNS-rebinding: коннектимся строго на уже проверенный IP (pin), а не
  резолвим повторно.
* Redaction: чистим и по именам ключей, и по ИЗВЕСТНЫМ значениям секретов до
  попадания в логи/ошибки/audit.
* Path confinement: канонический путь + запрет `..`/абсолютного побега +
  запрет выхода по symlink.

Второго secret store / event bus / policy здесь НЕ появляется — это чистые
функции, которые адаптеры вызывают перед обращением к сети/ФС.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from pathlib import Path
from urllib.parse import urlparse

import httpcore
import httpx

TOKEN_KEYS = re.compile(r"(token|secret|password|passwd|authorization|api[_-]?key|"
                        r"bearer|cookie|credential|client[_-]?secret|refresh[_-]?token)", re.I)


class PluginSecurityError(ValueError):
    """Отказ по политике безопасности адаптера (fail-closed)."""


# ----------------------------------------------------------------- redaction

def redact(value, *, secret_values: set[str] | None = None):
    """Рекурсивная чистка: по именам ключей И по известным значениям секретов.

    `secret_values` — конкретные строки (расшифрованные токены), которые НЕЛЬЗЯ
    печатать, даже если они попали в текст ошибки/URL/тела.
    """
    secrets = {s for s in (secret_values or set()) if s and len(s) >= 4}

    def _scrub_str(s: str) -> str:
        out = s
        for sv in secrets:
            if sv in out:
                out = out.replace(sv, "***REDACTED***")
        return out

    def _walk(v):
        if isinstance(v, dict):
            return {k: ("***REDACTED***" if TOKEN_KEYS.search(str(k)) else _walk(val))
                    for k, val in v.items()}
        if isinstance(v, (list, tuple)):
            t = [_walk(x) for x in v]
            return type(v)(t) if isinstance(v, tuple) else t
        if isinstance(v, str):
            return _scrub_str(v)
        return v

    return _walk(value)


# ----------------------------------------------------------------- SSRF

_PRIVATE_HOSTNAMES = {"localhost", "localhost.localdomain"}


def _ip_is_safe(ip: ipaddress._BaseAddress) -> bool:
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def validate_url(url: str, *, allow_private: bool = False,
                 allowed_hosts: set[str] | None = None) -> tuple[str, str]:
    """Синтаксис + литеральная проверка. Возвращает (url, host). Бросает при отказе.

    `allow_private=True` — осознанное исключение для доверенных локальных
    интеграций (например, локальный Ollama/n8n на 127.0.0.1), задаётся вызовом,
    а не приходит из пользовательского ввода.
    """
    p = urlparse(url)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise PluginSecurityError("invalid URL (scheme/host/userinfo)")
    host = p.hostname.lower().rstrip(".")
    if allowed_hosts is not None and not any(
            host == d or host.endswith("." + d) for d in allowed_hosts):
        raise PluginSecurityError("host not allowlisted")
    if allow_private:
        return url, host
    if host in _PRIVATE_HOSTNAMES or host.endswith(".local"):
        raise PluginSecurityError("local hostname blocked")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and not _ip_is_safe(ip):
        raise PluginSecurityError(f"non-global IP blocked: {ip}")
    return url, host


def resolve_pinned_ip(host: str, *, allow_private: bool = False) -> str:
    """Резолв имени → безопасный IP (анти-rebinding). Возвращает IP-строку.

    Проверяет ВСЕ резолвы; если хоть один небезопасный — отказ (иначе rebind на
    второй A-записи). Возвращаемый IP используется для pinned-коннекта.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise PluginSecurityError(f"DNS resolution failed: {exc}") from exc
    ips = {ai[4][0] for ai in infos}
    if not ips:
        raise PluginSecurityError("no addresses resolved")
    for raw in ips:
        ip = ipaddress.ip_address(raw)
        if not allow_private and not _ip_is_safe(ip):
            raise PluginSecurityError(f"resolved non-global address blocked: {ip}")
    return sorted(ips)[0]


try:
    from httpcore._backends.anyio import AnyIOBackend as _ConcreteNetworkBackend
except ImportError:  # httpcore сменил внутренности — базовый класс (fail-closed)
    _ConcreteNetworkBackend = httpcore.AsyncNetworkBackend


class _PinnedBackend(_ConcreteNetworkBackend):
    """Коннект строго на уже проверенный IP: DNS между валидацией и коннектом
    больше не участвует (анти-rebinding), SNI/TLS остаются на исходном имени."""

    def __init__(self, pins: dict[str, str]):
        super().__init__()
        self._pins = pins

    async def connect_tcp(self, host, port, timeout=None, local_address=None,
                          socket_options=None):
        return await super().connect_tcp(self._pins.get(host, host), port,
                                         timeout=timeout, local_address=local_address,
                                         socket_options=socket_options)


class PinnedTransport(httpx.AsyncHTTPTransport):
    """Обычный httpx-транспорт (весь glue httpx↔httpcore, дефолтная TLS-проверка),
    у которого connection-pool создаётся с pinned-резолвом (анти-rebinding)."""

    def __init__(self, pins: dict[str, str]):
        super().__init__()
        self._pool = httpcore.AsyncConnectionPool(network_backend=_PinnedBackend(pins))


async def safe_get(url: str, *, allow_private: bool = False,
                   allowed_hosts: set[str] | None = None,
                   max_bytes: int = 1_000_000, timeout: float = 15.0,
                   max_redirects: int = 3, headers: dict | None = None) -> httpx.Response:
    """GET с защитой от SSRF, DNS-rebinding и небезопасных redirect'ов.

    Каждый hop: валидация URL → резолв (ВСЕ адреса проверяются) → коннект на
    проверенный IP (hostname сохраняется для Host/SNI/сертификата). Redirect'ы
    не следуются автоматически — следующий hop валидируется заново. Тело
    читается потоково до `max_bytes`; превышение — отказ без аллокации всего
    тела.
    """
    hops = 0
    current = url
    pins: dict[str, str] = {}
    while True:
        _, host = validate_url(current, allow_private=allow_private, allowed_hosts=allowed_hosts)
        pins[host] = resolve_pinned_ip(host, allow_private=allow_private)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False,
                                     transport=PinnedTransport(pins)) as c:
            async with c.stream("GET", current,
                                headers={"User-Agent": "BossmanPlugins/1.0",
                                         **(headers or {})}) as r:
                if r.is_redirect:
                    loc = r.headers.get("location")
                    if not loc:
                        raise PluginSecurityError("redirect without Location")
                    hops += 1
                    if hops > max_redirects:
                        raise PluginSecurityError("too many redirects")
                    current = httpx.URL(r.request.url).join(loc).__str__()
                    continue  # revalidate next hop before following
                chunks: list[bytes] = []
                total = 0
                async for chunk in r.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise PluginSecurityError(f"response exceeds max_bytes={max_bytes}")
                    chunks.append(chunk)
                return httpx.Response(r.status_code,
                                      headers={k: v for k, v in r.headers.items()
                                               if k.lower() != "content-length"},
                                      content=b"".join(chunks))


# ----------------------------------------------------------------- path

def confine_path(root: str | Path, supplied: str, *, must_exist: bool = False) -> Path:
    """Канонический путь внутри root. Бросает при `..`/абсолютном/symlink-побеге."""
    root_p = Path(root).resolve()
    raw = Path(supplied)
    base = raw if raw.is_absolute() else (root_p / raw)
    candidate = base.resolve()  # .resolve() снимает symlink'и → побег по ссылке всплывёт здесь
    if candidate != root_p and root_p not in candidate.parents:
        raise PluginSecurityError("path escapes configured root")
    if must_exist and not candidate.exists():
        raise FileNotFoundError(str(candidate))
    return candidate
