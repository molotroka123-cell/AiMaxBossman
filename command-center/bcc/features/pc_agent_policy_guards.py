"""Политика Argon PC-Agent: охранные проверки GUI и браузера."""
from __future__ import annotations

import fnmatch
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
_DESKTOP_PAY_SEMANTICS = {"pay", "payment", "purchase", "wallet", "bank_transfer", "resume", "approve yourself"}
_DESKTOP_PAY_RE = r"(?iu)\b(оплат\w*|перевод\w*|купить|pay|purchase|wallet|bossman\s+approvals)\b"
_DESKTOP_CONSEQUENTIAL_RE = (
    r"(?iu)\b(удал\w*|delete|uninstall|форматир\w*|отправ\w*|"
    r"публик\w*|send|publish|submit)\b"
)
_DESKTOP_REVERSIBLE_ACTIONS = set(
    "launch focus focus_window click double_click type key hotkey scroll wait notepad_roundtrip".split()
)
_DESKTOP_LAUNCH_ALLOWED = {"notepad", "calc", "mspaint", "charmap"}
_BROWSER_CONSEQUENTIAL_CLICK_RE = r"(?iu)\b(удал\w*|отправ\w*|опублик\w*|delete|submit|send|publish)\b"
_EARN_T3_ACTIONS = set(
    "pay withdraw live_trade publish_external submit_bid "
    "fincept_enterprise fincept_live_broker vibe_trading_live".split()
)
_EARN_T3_MODES = {"live", "real", "real_money", "broker"}
_EARN_T3_EDITIONS = {"enterprise", "exclusive", "exclusive+", "exclusive_pro", "paid"}
_EARN_T0_ACTIONS = set(
    "status arena exam roi poker studio oss product weekly report blender fincept vibe_trading vibe_trade".split()
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


# ----------------------------------------------------------- Immutable Audit Log Guard
_IMMUTABLE_LOG_TARGETS_RE = re.compile(r"(?i)\b(?:audit\.jsonl(?:\.head)?|docs[\\/]audits|evidence)\b")
_MUTATION_OR_DELETE_RE = re.compile(
    r"(?iu)\b(удал\w*|очист\w*|перемест\w*|перезапиш\w*|"
    r"сотр\w*|затр\w*|переименов\w*|сделай\s+бэкап\s+и\s+удали|"
    r"бэкап\w*\s+и\s+удали|delete|remove|rm|del|clear|truncate|"
    r"move|mv|wipe|overwrite|erase|rename|ren|backup\s+and\s+delete)\b"
    r"|(?:Remove-Item|Clear-Content|Set-Content|Add-Content|Move-Item|Rename-Item|"
    r"Out-File|Tee-Object|\bdel\b|\brm\b|\bmv\b|\bmove\b|\bren\b|\btee\b|>|>>)"
)
_COPY_RE = re.compile(r"(?i)\b(?:copy|cp|copy-item|xcopy|robocopy)\b")
_PROTECTED_NAMES = ("audit.jsonl", "audit.jsonl.head")


def is_immutable_audit_log_target(text_or_path: str) -> bool:
    """Detect any attempt or command to delete, clear, move or overwrite audit.jsonl,
    audit.jsonl.head, docs/audits/ or evidence/."""
    if not text_or_path or not isinstance(text_or_path, str):
        return False
    s = text_or_path.strip()
    clean = s.replace("\\", "/").lower()
    if (
        clean in ("audit.jsonl", "audit.jsonl.head")
        or clean.endswith("/audit.jsonl")
        or clean.endswith("/audit.jsonl.head")
        or clean.startswith("docs/audits")
        or "/docs/audits" in clean
        or clean.startswith("evidence/")
        or "/evidence/" in clean
    ):
        return True
    if _IMMUTABLE_LOG_TARGETS_RE.search(s) and _MUTATION_OR_DELETE_RE.search(s):
        return True
    glued = re.sub(r"[\s'\"+()`]", "", s.lower())
    if any(n in glued for n in _PROTECTED_NAMES) and _MUTATION_OR_DELETE_RE.search(s):
        return True
    low = s.lower()
    if "audit" in low and "jsonl" in low and _MUTATION_OR_DELETE_RE.search(s):
        return True
    if _MUTATION_OR_DELETE_RE.search(s):
        for token in re.split(r"[\s,;|&()'\"]+", s):
            if "*" in token or "?" in token:
                base = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
                if re.search(r"[a-z]", base) and any(fnmatch.fnmatchcase(n, base) for n in _PROTECTED_NAMES):
                    return True
    if _COPY_RE.search(s):
        last = re.split(r"\s+", s.rstrip())[-1].strip("'\"").replace("\\", "/").lower()
        tail = last.rsplit("/", 1)[-1]
        if tail in _PROTECTED_NAMES or tail.startswith("audit.jsonl:"):
            return True
    return False


# ----------------------------------------------------------- PowerShell AST & Token Gate
_POWERSHELL_BACKTICK_OBFUSCATION_RE = re.compile(r"[A-Za-z]`+[A-Za-z]")
_POWERSHELL_STRING_CONCAT_RE = re.compile(r"['\"][A-Za-z0-9_-]+['\"]\s*\+\s*['\"][A-Za-z0-9_-]+['\"]")
_POWERSHELL_IEX_RE = re.compile(r"(?i)\b(?:iex|invoke-expression)\b")
_POWERSHELL_ENCODED_CMD_RE = re.compile(r"(?i)(?:^|\s)-(?:e|ec|enc|encodedcommand)\b")
_POWERSHELL_DOWNLOAD_RE = re.compile(r"(?i)\b(?:downloadstring|downloadfile|downloaddata)\b")
_POWERSHELL_CALL_OP_RE = re.compile(r"(?:^|[;\s|&])[&.]\s*(?:\$[A-Za-z_]\w*|\(|\{)")
_POWERSHELL_DYNAMIC_INVOKE_RE = re.compile(
    r"(?i)(?:\binvokescript\b|\[scriptblock\]\s*::\s*create|\.invoke\s*\(|\.\s*delete\s*\()"
)
_POWERSHELL_FORMAT_OP_RE = re.compile(r"(?i)['\"][^'\"]*\{\d+\}[^'\"]*['\"]\s*-f\b")


def validate_powershell_ast_or_tokens(cmd: str) -> tuple[bool, str]:
    """Validate PowerShell commands against obfuscation, encoding, string concatenation,
    call-operator indirection, InvokeScript/ScriptBlock::Create, and downloaders."""
    if not cmd or not isinstance(cmd, str):
        return True, "ok"
    if _POWERSHELL_BACKTICK_OBFUSCATION_RE.search(cmd):
        return False, "powershell_backtick_obfuscation"
    if _POWERSHELL_ENCODED_CMD_RE.search(cmd):
        return False, "powershell_encoded_command"
    if _POWERSHELL_DOWNLOAD_RE.search(cmd):
        return False, "powershell_download_string"
    if _POWERSHELL_IEX_RE.search(cmd):
        return False, "powershell_iex_execution"
    if _POWERSHELL_STRING_CONCAT_RE.search(cmd):
        return False, "powershell_string_concatenation_obfuscation"
    if _POWERSHELL_CALL_OP_RE.search(cmd):
        return False, "powershell_call_operator_indirection"
    if _POWERSHELL_DYNAMIC_INVOKE_RE.search(cmd):
        return False, "powershell_dynamic_scriptblock_invoke"
    if _POWERSHELL_FORMAT_OP_RE.search(cmd):
        return False, "powershell_format_operator_obfuscation"
    return True, "ok"


# ----------------------------------------------------------- Safe Windows Canonical Path
def canonicalize_windows_path_safe(path: str | Any, *, reject_dangerous: bool = False) -> str:
    """Canonicalize a Windows path resolving 8.3 names, junctions, symlinks, and GLOBALROOT."""
    import os
    import sys
    raw = str(path or "").strip()
    if not raw:
        return ""

    if reject_dangerous:
        from ..security.win_paths import _screen
        _screen(raw)

    if "\\globalroot" in raw.lower() or "/globalroot" in raw.lower():
        raw = re.sub(r"(?i)^[\\/]+\?[\\/]globalroot", "", raw)

    # Strip NTFS alternate data stream suffix after drive colon if present
    s_norm = raw.replace("\\", "/")
    has_drive = len(s_norm) >= 2 and s_norm[0].isalpha() and s_norm[1] == ":"
    if ":" in (s_norm[2:] if has_drive else s_norm):
        head_drive = raw[:2] if has_drive else ""
        rest_part = (raw[2:] if has_drive else raw).split(":", 1)[0]
        raw = head_drive + rest_part

    if sys.platform == "win32" and os.path.exists(raw):
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            FILE_READ_ATTRIBUTES = 0x0080
            FILE_SHARE_READ = 1
            FILE_SHARE_WRITE = 2
            FILE_SHARE_DELETE = 4
            OPEN_EXISTING = 3
            FILE_FLAG_BACKUP_SEMANTICS = 0x02000000

            handle = k32.CreateFileW(
                raw,
                FILE_READ_ATTRIBUTES,
                FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                None,
                OPEN_EXISTING,
                FILE_FLAG_BACKUP_SEMANTICS,
                None,
            )
            if handle != -1 and handle != 0:
                try:
                    buf = ctypes.create_unicode_buffer(1024)
                    res = k32.GetFinalPathNameByHandleW(handle, buf, 1024, 0)
                    if res > 0:
                        canon = buf.value
                        if canon.startswith("\\\\?\\UNC\\"):
                            canon = "\\\\" + canon[8:]
                        elif canon.startswith("\\\\?\\"):
                            canon = canon[4:]
                        return os.path.normpath(canon)
                finally:
                    k32.CloseHandle(handle)
        except Exception:
            pass

    try:
        return os.path.normpath(os.path.realpath(raw))
    except Exception:
        return os.path.normpath(raw)


