"""Read-only active-tab info over Chrome DevTools Protocol (master prompt 28.10, task 4).

Computer-use must never stop because the URL cannot be read from the Windows window title. Chrome started with
`--remote-debugging-port=9222` lists its tabs at `/json/list`, most recently activated first, with the REAL url and title.
Nothing here clicks, types or navigates. Safety:
  * the CDP endpoint must be loopback (no SSRF through an env var);
  * the URL is returned WITHOUT query and fragment (Pult links can carry tokens);
  * the title is page-controlled text: control characters are stripped and it is capped, and the tool is marked
    external_output so the engine treats it as untrusted data;
  * build_sha is read from `/health` of the tab's own origin, only when that origin is loopback.
If CDP is not reachable the answer says so and how to enable it; it never guesses.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import urllib.request
from typing import Any
from urllib.parse import urlsplit

DEFAULT_CDP = "http://127.0.0.1:9222"
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_SKIP_SCHEMES = ("devtools:", "chrome-extension:", "chrome:", "edge:", "about:", "chrome-untrusted:")


class CdpUnavailable(RuntimeError):
    pass


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def cdp_base() -> str:
    base = os.environ.get("BOSSMAN_CDP_URL", DEFAULT_CDP).strip().rstrip("/")
    if not _is_loopback(urlsplit(base).hostname):
        raise CdpUnavailable("BOSSMAN_CDP_URL must point to a loopback address")
    return base


def _get_json(url: str, timeout: float = 3.0) -> Any:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "bossman-cdp/1"}), timeout=timeout) as r:
        return json.loads(r.read(2_000_000).decode("utf-8", "replace"))


def clean_url(url: str) -> str:
    p = urlsplit(url)
    if not p.scheme:
        return ""
    netloc = p.netloc.rsplit("@", 1)[-1]                 # drop user:password@
    return f"{p.scheme}://{netloc}{p.path}" if netloc else f"{p.scheme}:{p.path}"


def clean_title(title: Any) -> str:
    return _CTRL.sub(" ", str(title or "")).strip()[:200]


def pick_active(targets: list[dict[str, Any]]) -> dict[str, Any] | None:
    pages = [t for t in targets if isinstance(t, dict) and t.get("type") == "page"
             and str(t.get("url", "")).lower().startswith(("http:", "https:", "file:"))
             and not str(t.get("url", "")).lower().startswith(_SKIP_SCHEMES)]
    return pages[0] if pages else None


def build_sha_of(origin_url: str, timeout: float = 3.0) -> str | None:
    p = urlsplit(origin_url)
    if p.scheme not in ("http", "https") or not _is_loopback(p.hostname):
        return None
    try:
        host = f"[{p.hostname}]" if ":" in (p.hostname or "") else p.hostname      # origin rebuilt WITHOUT user:password@
        d = _get_json(f"{p.scheme}://{host}{(':' + str(p.port)) if p.port else ''}/health", timeout)
    except Exception:
        return None
    sha = d.get("build_sha") if isinstance(d, dict) else None
    return sha if isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{7,64}", sha) else None


def active_tab_info(base: str | None = None) -> dict[str, Any]:
    base = base or cdp_base()
    try:
        targets = _get_json(f"{base}/json/list")
    except Exception as exc:
        raise CdpUnavailable(
            f"CDP недоступен на {base} ({type(exc).__name__}). Запустите Chrome с --remote-debugging-port=9222; "
            "URL по заголовку окна я не угадываю.") from exc
    tab = pick_active(targets if isinstance(targets, list) else [])
    if tab is None:
        raise CdpUnavailable("в Chrome нет открытой http(s)-вкладки")
    return {"url": clean_url(str(tab.get("url", ""))), "title": clean_title(tab.get("title")),
            "build_sha": build_sha_of(str(tab.get("url", ""))), "target_id": str(tab.get("id", ""))[:64],
            "tabs_open": len([t for t in targets if isinstance(t, dict) and t.get("type") == "page"]),
            "source": "cdp:/json/list (most recently activated first)"}
