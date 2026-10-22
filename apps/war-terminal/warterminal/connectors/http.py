"""Unified allowlisted egress HTTP client for War Terminal connectors (P2.14 & P2.15).

Enforces:
- Strict HTTPS-only scheme and closed hostname allowlist (`ALLOWED_EGRESS_HOSTS`).
- DNS resolution pinning & validation: blocks loopback, RFC1918 private, link-local,
  multicast, reserved, Tailscale CGNAT (`100.64.0.0/10`), and cloud metadata (`169.254.169.254`) IPs.
- Safe redirect handling: `follow_redirects=False` on every hop; any 3xx `Location` target
  must pass `validate_redirect_target` (allowlisted host + public DNS IP) before following.
- Curl fallback never uses `-L` (unvalidated redirects) or shell execution.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import ssl
import subprocess
from urllib.parse import urljoin, urlsplit

import httpx

try:                                    # optional
    import truststore
    _VERIFY = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
except Exception:                       # noqa: BLE001
    _VERIFY = True

UA = "bossman-war-terminal/0.1 (+owner research; contact via owner)"
MAX_REDIRECT_HOPS = 3

TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")

ALLOWED_EGRESS_HOSTS: frozenset[str] = frozenset({
    "api.ted.europa.eu",
    "ted.europa.eu",
    "api.sam.gov",
    "api.usaspending.gov",
    "search.worldbank.org",
    "api.worldbank.org",
    "public.api.openprocurement.org",
    "prozorro.gov.ua",
    "isvz.nipez.cz",
    "nen.nipez.cz",
    "vvz.nipez.cz",
    "api.gdeltproject.org",
    "ucdp.uu.se",
    "www.treasury.gov",
    "ofac.treasury.gov",
    "scsanctions.un.org",
    "www.cnb.cz",
    "www.ecb.europa.eu",
    "api.unhcr.org",
    "api.frankfurter.dev",
    "api.frankfurter.app",
    "nominatim.openstreetmap.org",
    "comtradeapi.un.org",
})


def _assert_public_ip(ip_str: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    ip = ipaddress.ip_address(ip_str.strip().strip("[]"))
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv4Address) and ip in TAILSCALE_CGNAT)
    ):
        raise ValueError(f"Egress blocked: non-public IP address {ip}")
    return ip


def validate_egress_url(url: str, *, resolve_dns: bool = True) -> dict[str, object]:
    """Validate URL against HTTPS-only allowlist and verify all DNS-resolved IPs are global public IPs."""
    raw = str(url or "").strip()
    if not raw or any(ch in raw for ch in ("\r", "\n", "\x00")):
        raise ValueError("Invalid or control-char URL rejected")
    parts = urlsplit(raw)
    if parts.scheme.lower() != "https":
        raise ValueError(f"Egress blocked: only https:// is allowed (got {parts.scheme!r})")
    if parts.username or parts.password:
        raise ValueError("Egress blocked: credentials in URL are forbidden")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise ValueError("Egress blocked: missing hostname")
    try:
        # Reject raw IP literals even before allowlist check
        _assert_public_ip(host)
        raise ValueError(f"Egress blocked: direct IP literal {host!r} is forbidden")
    except ValueError as vex:
        if "non-public IP" in str(vex) or "direct IP literal" in str(vex):
            raise

    if host not in ALLOWED_EGRESS_HOSTS:
        raise ValueError(f"Egress blocked: host {host!r} is not in ALLOWED_EGRESS_HOSTS")

    port = parts.port or 443
    if port != 443:
        raise ValueError(f"Egress blocked: non-standard port {port} is forbidden")

    pinned_ips: list[str] = []
    if resolve_dns:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ValueError(f"DNS resolution failed for {host}: {exc}") from exc
        if not infos:
            raise ValueError(f"DNS resolution returned no addresses for {host}")
        for info in infos:
            sockaddr = info[4]
            ip_str = str(sockaddr[0])
            _assert_public_ip(ip_str)
            if ip_str not in pinned_ips:
                pinned_ips.append(ip_str)

    return {"url": raw, "host": host, "port": port, "pinned_ips": pinned_ips}


def validate_redirect_target(current_url: str, location: str, *, resolve_dns: bool = True) -> str:
    """Resolve and validate a 3xx redirect target against the strict egress allowlist and public IP rules."""
    if not location:
        raise ValueError("Empty redirect Location header")
    resolved_target = urljoin(current_url, location.strip())
    validate_egress_url(resolved_target, resolve_dns=resolve_dns)
    return resolved_target


def _curl(method: str, url: str, body: dict | None, headers: dict | None, timeout: int, *, raw_bytes: bool = False):
    meta = validate_egress_url(url, resolve_dns=True)
    host = str(meta["host"])
    pinned_ips = list(meta["pinned_ips"])  # type: ignore[arg-type]
    # Note: --proto =https forbids protocol downgrade; no -L (never follow unvalidated redirects); --resolve pins DNS!
    cmd = ["curl", "-s", "--proto", "=https", "-m", str(int(timeout)), "-X", method]
    if pinned_ips:
        cmd += ["--resolve", f"{host}:443:{pinned_ips[0]}"]
    cmd += [url, "-H", f"User-Agent: {UA}"]
    for k, v in (headers or {}).items():
        if "\r" in str(k) or "\n" in str(k) or "\r" in str(v) or "\n" in str(v):
            raise ValueError("Invalid header in egress request")
        cmd += ["-H", f"{k}: {v}"]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    p = subprocess.run(cmd, capture_output=True, timeout=int(timeout) + 15, shell=False)
    if p.returncode != 0:
        raise RuntimeError(f"curl exit {p.returncode}")
    if raw_bytes:
        return p.stdout
    return json.loads(p.stdout.decode("utf-8", "replace"))


def _request_with_safe_redirects(
    method: str,
    url: str,
    *,
    params: dict | None = None,
    json_body: dict | None = None,
    headers: dict | None = None,
    timeout: int = 60,
) -> httpx.Response:
    current_url = str(httpx.URL(url, params=params)) if params else url
    req_headers = {"User-Agent": UA, **(headers or {})}
    for _hop in range(MAX_REDIRECT_HOPS + 1):
        validate_egress_url(current_url, resolve_dns=True)
        resp = httpx.request(
            method,
            current_url,
            json=json_body if method == "POST" else None,
            headers=req_headers,
            timeout=timeout,
            verify=_VERIFY,
            follow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("location", "")
            current_url = validate_redirect_target(current_url, loc, resolve_dns=True)
            if resp.status_code == 303:
                method = "GET"
                json_body = None
            continue
        resp.raise_for_status()
        return resp
    raise ValueError(f"Exceeded MAX_REDIRECT_HOPS ({MAX_REDIRECT_HOPS})")


def post_json(url: str, body: dict, headers: dict | None = None, timeout: int = 60) -> dict:
    validate_egress_url(url, resolve_dns=True)
    try:
        r = _request_with_safe_redirects("POST", url, json_body=body, headers=headers, timeout=timeout)
        return r.json()
    except (httpx.ConnectError, ssl.SSLError):
        return _curl("POST", url, body, headers, timeout)


def get_bytes(url: str, timeout: int = 240) -> bytes:
    validate_egress_url(url, resolve_dns=True)
    try:
        r = _request_with_safe_redirects("GET", url, timeout=timeout)
        return r.content
    except (httpx.ConnectError, ssl.SSLError):
        return _curl("GET", url, None, None, timeout, raw_bytes=True)


def get_json(url: str, params: dict | None = None, headers: dict | None = None, timeout: int = 60) -> dict:
    q_url = str(httpx.URL(url, params=params)) if params else url
    validate_egress_url(q_url, resolve_dns=True)
    try:
        r = _request_with_safe_redirects("GET", q_url, headers=headers, timeout=timeout)
        return r.json()
    except (httpx.ConnectError, ssl.SSLError):
        return _curl("GET", q_url, None, headers, timeout)
