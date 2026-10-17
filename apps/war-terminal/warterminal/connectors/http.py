"""Tiny HTTP layer. Uses the Windows/OS trust store (truststore) when present: some networks re-sign TLS and Python's bundled CAs fail.
Falls back to curl (OS trust store) on a TLS verification error. Verification is never disabled."""
from __future__ import annotations

import json
import ssl
import subprocess

import httpx

try:                                    # optional
    import truststore
    _VERIFY = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
except Exception:                       # noqa: BLE001
    _VERIFY = True

UA = "bossman-war-terminal/0.1 (+owner research; contact via owner)"


def _curl(method: str, url: str, body: dict | None, headers: dict | None, timeout: int):
    cmd = ["curl", "-s", "-m", str(timeout), "-X", method, url, "-H", f"User-Agent: {UA}"]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    p = subprocess.run(cmd, capture_output=True, timeout=timeout + 15)
    if p.returncode != 0:
        raise RuntimeError(f"curl exit {p.returncode}")
    return json.loads(p.stdout.decode("utf-8", "replace"))


def post_json(url: str, body: dict, headers: dict | None = None, timeout: int = 60) -> dict:
    try:
        r = httpx.post(url, json=body, headers={"User-Agent": UA, **(headers or {})}, timeout=timeout, verify=_VERIFY)
        r.raise_for_status()
        return r.json()
    except (httpx.ConnectError, ssl.SSLError):
        return _curl("POST", url, body, headers, timeout)


def get_bytes(url: str, timeout: int = 240) -> bytes:
    try:
        r = httpx.get(url, headers={"User-Agent": UA}, timeout=timeout, verify=_VERIFY, follow_redirects=True)
        r.raise_for_status()
        return r.content
    except (httpx.ConnectError, ssl.SSLError):
        p = subprocess.run(["curl", "-s", "-L", "-m", str(timeout), url, "-H", f"User-Agent: {UA}"], capture_output=True, timeout=timeout + 15)
        if p.returncode != 0:
            raise RuntimeError(f"curl exit {p.returncode}")
        return p.stdout


def get_json(url: str, params: dict | None = None, headers: dict | None = None, timeout: int = 60) -> dict:
    try:
        r = httpx.get(url, params=params, headers={"User-Agent": UA, **(headers or {})}, timeout=timeout, verify=_VERIFY)
        r.raise_for_status()
        return r.json()
    except (httpx.ConnectError, ssl.SSLError):
        q = httpx.URL(url, params=params)
        return _curl("GET", str(q), None, headers, timeout)
