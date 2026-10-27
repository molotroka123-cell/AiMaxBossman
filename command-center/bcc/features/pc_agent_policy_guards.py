"""Политика Argon PC-Agent: охранные проверки GUI и браузера."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from .pc_agent_policy_consts import PolicyVerdict
from .pc_agent_policy_paths import scrub_secrets

BROWSER_PAY_CLICK_RE = (
    r"(?iu)\b(оплат\w*|купить|buy|pay|checkout|purchase|place\s+order"
    r"|confirm\s+(?:purchase|order|payment)|оформить\s+заказ"
    r"|подтвердить\s+(?:заказ|оплату)|transfer\s+funds|перевести\s+средства)\b"
)
# клик или ввод по подтверждению (Approve, /pc_yes): сам себя не одобряем
_SELF_APPROVAL = "Одобряет только владелец (T3)."
APPROVAL_UI_RE = r"(?iu)(?:\bapprove\w*\b|одобр\w*|разреш\w*|подтверд\w*|(?<!\w)/?pc_yes\b)"
RUN_DIALOG_HOTKEY_RE = (
    r"(?i)(?:\b(?:win|windows|meta|super)\s*\+\s*(?:r|x)\b"
    r"|alt\s*\+\s*f2\b|ctrl\s*\+\s*shift\s*\+\s*esc\b)"
)
_INTERNAL_HOST_RE = r"(?:127\.|10\.|192\.168\.|169\.254\.|172\.(?:1[6-9]|2\d|3[01])\.)"
_DESKTOP_KEYS = ("text", "keys", "key", "value", "hotkey")


def desktop_guard_verdict(
    action: str, target: str, semantic: str, args: dict[str, Any]
) -> PolicyVerdict | None:
    typed = " ".join(str(args.get(k) or "") for k in _DESKTOP_KEYS)
    probe = f"{target} {semantic} {typed}"
    if action in {"click", "double_click", "type", "key", "hotkey"} and re.search(APPROVAL_UI_RE, probe):
        return PolicyVerdict("T3", "deny", "self_approval_forbidden", _SELF_APPROVAL, True)
    if action in {"key", "hotkey", "type"} and re.search(RUN_DIALOG_HOTKEY_RE, probe):
        return PolicyVerdict("T2", "ask", "desktop_run_dialog_hotkey", "Диалог запуска (T2).", True)
    return None


def browser_guard_verdict(action: str, url: str, label: str) -> PolicyVerdict | None:
    if action in {"open", "navigate"} and url:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        is_safe_liveness = (
            action == "open"
            and host in {"127.0.0.1", "localhost"}
            and parsed.path.rstrip("/") == "/health/live"
            and not parsed.query
        )
        if not is_safe_liveness and (
            host in {"localhost", "::1"} or host.endswith(".local") or re.match(_INTERNAL_HOST_RE, host)
        ):
            reason = f"Внутренний адрес {host} (T2)."
            return PolicyVerdict("T2", "ask", "browser_internal_host", reason, True)
        if scrub_secrets(url) != url:
            return PolicyVerdict("T3", "deny", "browser_secret_in_url", "Секрет в адресе (T3).", True)
    if action in {"click", "type"} and re.search(APPROVAL_UI_RE, label):
        return PolicyVerdict("T3", "deny", "self_approval_forbidden", _SELF_APPROVAL, True)
    return None
