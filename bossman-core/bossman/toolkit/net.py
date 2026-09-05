"""http — вызовы API: статус + ключевые поля по схеме, не сырой JSON (≤2K токенов);
сырой ответ — в файл. Работает только у агентов, которым выдан.

F-004 (SSRF): URL контролирует модель, поэтому у инструмента есть собственная
egress-политика, а не надежда на сеть:
- схема только http/https;
- хост резолвится и проверяется: loopback, link-local, RFC1918, ULA, 0.0.0.0,
  «metadata»-адреса облаков — запрет (пивот на gateway/core/админ-порты и
  кража metadata-токенов);
- редиректы не следуем автоматически: до 3 переходов вручную, каждый хоп
  проверяется той же политикой (redirect-to-private — запрет);
- владелец может расширить: BOSSMAN_HTTP_ALLOW_HOSTS (список хостов через
  запятую; ".example.com" = все поддомены) — такие хосты разрешены даже если
  резолвятся в приватную сеть; BOSSMAN_HTTP_ALLOW_PRIVATE=1 — снять запрет
  приватных/loopback-адресов целиком (metadata-endpoint'ы остаются под запретом,
  их можно открыть только явным перечислением в ALLOW_HOSTS);
- confirm_default=True: это rights=send, и первое обращение к произвольному
  хосту видит владелец. Освобождение от подтверждения для allowlist-хостов
  статичной схемой ToolDef не выражается (runner читает confirm_default без
  аргументов) — владелец снимает его грантом агента (confirm: false) вместе с
  ALLOW_HOSTS; политика хостов при этом продолжает действовать.

Соединение закреплено за проверенным IP; ответы ограничены по объёму и времени.
"""
from __future__ import annotations

import asyncio
import zlib
import ipaddress
import json
import os
import socket
import uuid
from urllib.parse import urljoin, urlsplit

import httpx

from bossman_shared.http_transport import PinnedTransport
from bossman_v3.memory.assembler import redact, redact_data

from . import ToolContext, ToolDef, ToolResult, clip, compact_json, register

ALLOWED_SCHEMES = frozenset({"http", "https"})
MAX_REDIRECTS = 3
MAX_RESPONSE_BYTES = 2_000_000

# Метаданные облаков и прочие «магические» хосты — всегда под запретом, если
# владелец не перечислил их явно в BOSSMAN_HTTP_ALLOW_HOSTS.
METADATA_HOSTS = frozenset({
    "metadata.google.internal", "metadata", "instance-data", "instance-data.ec2.internal",
    "169.254.169.254", "fd00:ec2::254", "100.100.100.200",  # AWS/GCP/Azure, EC2 IPv6, Alibaba
})
METADATA_NETS = (
    ipaddress.ip_network("169.254.169.254/32"),
    ipaddress.ip_network("fd00:ec2::254/128"),
    ipaddress.ip_network("100.100.100.200/32"),
)

# Транспорт для тестов (MockTransport без сети). None = обычный httpx.
_TRANSPORT: httpx.AsyncBaseTransport | None = None


class EgressDenied(ValueError):
    """URL нарушает egress-политику инструмента http — запрос не отправлен."""


def _allow_hosts() -> list[str]:
    raw = os.environ.get("BOSSMAN_HTTP_ALLOW_HOSTS", "")
    return [h.strip().lower().rstrip(".") for h in raw.split(",") if h.strip()]


def _allow_private() -> bool:
    return os.environ.get("BOSSMAN_HTTP_ALLOW_PRIVATE", "").strip().lower() in ("1", "true", "yes")


def _host_allowlisted(host: str) -> bool:
    host = host.lower().rstrip(".")
    for pat in _allow_hosts():
        if pat.startswith("."):
            if host == pat[1:] or host.endswith(pat):
                return True
        elif host == pat:
            return True
    return False


def _resolve_host(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Все адреса хоста (A/AAAA). Литеральный IP — сам себе ответ. Сбой DNS →
    пустой список → отказ (fail-closed): к неизвестному не ходим."""
    try:
        return [ipaddress.ip_address(host.strip("[]"))]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return []
    out = []
    for info in infos:
        try:
            out.append(ipaddress.ip_address(info[4][0].split("%")[0]))
        except ValueError:
            continue
    return out


def _is_metadata(ip) -> bool:
    return any(ip in net for net in METADATA_NETS)


def _is_private(ip) -> bool:
    """Loopback / link-local / RFC1918 / ULA / unspecified / multicast / reserved.
    IPv4-mapped IPv6 (::ffff:127.0.0.1) разворачивается в IPv4."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_unspecified
            or ip.is_multicast or ip.is_reserved or not ip.is_global)


def check_url(url: str, *, pins: dict[str, str] | None = None) -> str:
    """Проверить URL по egress-политике. Возвращает нормализованный URL или
    поднимает EgressDenied. Вызывается для исходного URL и КАЖДОГО редиректа."""
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise EgressDenied(f"схема '{scheme or '-'}' запрещена (только http/https)")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise EgressDenied("в URL нет хоста")
    if parts.username or parts.password:
        raise EgressDenied("учётные данные в URL запрещены")
    allowlisted = _host_allowlisted(host)
    if allowlisted and pins is None:
        return url  # владелец перечислил хост явно — его решение
    if not allowlisted and (host in METADATA_HOSTS or host in ("localhost", "0.0.0.0") or host.endswith(".localhost")):
        raise EgressDenied(f"хост '{host}' запрещён политикой egress (loopback/metadata)")
    addrs = _resolve_host(host)
    if not addrs:
        raise EgressDenied(f"хост '{host}' не резолвится — запрос не отправлен")
    for ip in addrs:
        if not allowlisted and _is_metadata(ip):
            raise EgressDenied(f"хост '{host}' → {ip}: metadata-endpoint облака запрещён")
        if not allowlisted and _is_private(ip) and not _allow_private():
            raise EgressDenied(
                f"хост '{host}' → {ip}: приватный/loopback-адрес запрещён "
                f"(BOSSMAN_HTTP_ALLOW_PRIVATE=1 или BOSSMAN_HTTP_ALLOW_HOSTS для исключений)")
    if pins is not None:
        pins[host] = str(addrs[0])
        pins[httpx.URL(url).host] = str(addrs[0])
    return url


def _pick(data, fields: list[str]):
    if not fields or not isinstance(data, dict):
        return data
    return {k: data.get(k) for k in fields if k in data}


async def _request_checked(client: httpx.AsyncClient, method: str, url: str, *,
                           json_body, params) -> httpx.Response:
    """Запрос с ручным следованием редиректам: не более MAX_REDIRECTS хопов,
    каждый хоп заново проходит check_url (redirect-to-private → отказ)."""
    pins = getattr(client, "_bossman_pins", None)
    if pins is None:
        raise EgressDenied("HTTP client has no pinned destination authority")
    hops = 0
    while True:
        # DNS must not block the event loop or bypass the total request deadline.
        # A cancelled worker gets its own pins, never the live transport authority.
        resolved_pins = {}
        await asyncio.to_thread(check_url, url, pins=resolved_pins)
        pins.update(resolved_pins)
        async with client.stream(method, url, json=json_body, params=params,
                                 headers={"Accept-Encoding": "identity"}, follow_redirects=False) as resp:
            location = resp.headers.get("location")
            if resp.status_code not in (301, 302, 303, 307, 308) or not location:
                content = await _bounded_body(resp)
                headers = {k: v for k, v in resp.headers.items()
                           if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")}
                return httpx.Response(resp.status_code, headers=headers, content=content, request=resp.request)
            hops += 1
            if hops > MAX_REDIRECTS:
                raise EgressDenied(f"too many redirects (> {MAX_REDIRECTS})")
            url = urljoin(url, location)
            if resp.status_code in (301, 302, 303) and method.upper() != "GET":
                method, json_body = "GET", None
            params = None


async def _bounded_body(response: httpx.Response) -> bytes:
    if response.is_stream_consumed:
        data = response.content
        if len(data) > MAX_RESPONSE_BYTES:
            raise EgressDenied("response exceeds decompressed byte budget")
        return data
    encoding = response.headers.get("content-encoding", "identity").lower()
    if encoding not in ("identity", "gzip", "deflate", ""):
        raise EgressDenied("unsupported content encoding for bounded HTTP input")
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS) \
        if encoding in ("gzip", "deflate") else None
    data = bytearray()
    raw_bytes = 0
    async for chunk in response.aiter_raw(chunk_size=65536):
        raw_bytes += len(chunk)
        if raw_bytes > MAX_RESPONSE_BYTES:
            raise EgressDenied("response exceeds transfer byte budget")
        remaining = MAX_RESPONSE_BYTES - len(data) + 1
        data.extend(decoder.decompress(chunk, remaining) if decoder else chunk)
        if len(data) > MAX_RESPONSE_BYTES or (decoder and decoder.unconsumed_tail):
            raise EgressDenied("response exceeds decompressed byte budget")
        if decoder and decoder.unused_data:
            raise EgressDenied("multiple compressed members refused")
    if decoder and not decoder.eof:
        raise EgressDenied("incomplete compressed response")
    return bytes(data)


async def http(args: dict, ctx: ToolContext) -> ToolResult:
    method = str(args.get("method", "GET") or "GET").upper()
    url = str(args["url"])
    try:
        pins = {}
        async with asyncio.timeout(60), httpx.AsyncClient(
                timeout=60, transport=_TRANSPORT or PinnedTransport(pins), trust_env=False) as client:
            client._bossman_pins = pins
            resp = await _request_checked(client, method, url,
                                          json_body=args.get("json"), params=args.get("params"))
    except (EgressDenied, TimeoutError, zlib.error) as exc:
        # Отказ политики — данные для модели, не падение петли; в сеть не ходили.
        return ToolResult(f"http: запрос отклонён egress-политикой: {exc}",
                          one_line=f"http {method} {url[:60]} → запрещено", error=True)
    raw_path = ctx.workdir / "assets" / "logs" / f"http-{uuid.uuid4().hex[:8]}.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        clean = redact_data(resp.json())
        persisted = json.dumps(clean, ensure_ascii=False)
        body = compact_json(_pick(clean, args.get("fields") or []))
    except json.JSONDecodeError:
        persisted = redact(resp.text)
        body = persisted
    raw_path.write_text(persisted, encoding="utf-8")
    body, cut = clip(f"статус: {resp.status_code}\n{body}", 2000)
    rel = raw_path.relative_to(ctx.workdir)
    return ToolResult(body, one_line=f"http {method} {url[:60]} → {resp.status_code}",
                      truncated=True, more=f"fs.read(path='{rel}')",
                      error=resp.status_code >= 400)


register(ToolDef("http", "HTTP-запрос: статус + ключевые поля (fields) вместо сырого JSON.",
                 "send", http,
                 params={"url": {"type": "string"}, "method": {"type": "string"},
                         "json": {"type": "object"}, "params": {"type": "object"},
                         "fields": {"type": "array", "items": {"type": "string"}}},
                 required=["url"], token_limit=2000,
                 # F-004: rights=send к произвольному хосту — подтверждение по умолчанию.
                 confirm_default=True))
