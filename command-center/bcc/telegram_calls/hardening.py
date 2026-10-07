"""Secret hygiene for the calls module: owner-only ACL check, log redaction, restart-safe writes.

Why this exists (owner requirement): the Fernet Vault protects the api_hash / session at rest, but a session string
is the whole Telegram account. Before it is used we verify (1) who can read the files, (2) that nothing secret is
written to a log, (3) that the files behave after a restart / interrupted write.

* ``check_owner_only`` — POSIX: mode has no group/other bits and the file belongs to the current user. Windows: parses
  ``icacls`` output and rejects Everyone / BUILTIN\\Users / Authenticated Users. Pure parser is testable off Windows.
* ``RedactingFormatter`` — redacts registered secret values and secret-shaped strings from the WHOLE formatted record,
  tracebacks included (a ``logging.Filter`` cannot see those).
* ``configure_worker_logging`` — worker log file (0600, size-capped), noisy libraries pinned to WARNING.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import re
import subprocess
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

MARK = "[REDACTED]"
_MIN_SECRET_LEN = 6

_HEX32 = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{32}(?![0-9A-Fa-f])")
_LONG_TOKEN = re.compile(r"(?<![A-Za-z0-9_\-])[A-Za-z0-9_\-+/=]{40,}(?![A-Za-z0-9_\-+/=])")
_PHONE = re.compile(r"(?<![\w])\+\d[\d\s\-()]{7,16}\d")
_CODE_AFTER_WORD = re.compile(r"(?i)\b(code|код|password|пароль|2fa)\b(\s*[:=]?\s*)([^\s,;]{3,})")
_TG_BOT = re.compile(r"(?<!\d)\d{6,16}:[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])")

#: principals that may legitimately hold access on Windows (the current user is added at runtime)
_WIN_ALLOWED = {"nt authority\\system", "system", "builtin\\administrators", "administrators",
                "owner rights", "creator owner"}
_WIN_DENIED = ("everyone", "builtin\\users", "users", "nt authority\\authenticated users", "authenticated users",
               "nt authority\\interactive", "interactive")


def _localized_allowed() -> set[str]:
    """SYSTEM and Administrators as this Windows prints them (Russian: СИСТЕМА / Администраторы), resolved by
    well-known SID so the check does not depend on the display language. Empty off Windows or on failure."""
    if os.name != "nt":
        return set()
    import ctypes
    from ctypes import wintypes
    names: set[str] = set()
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    for sid_type in (22, 26, 71, 27):         # SYSTEM, Administrators, OWNER RIGHTS, CREATOR OWNER
        try:
            sid = ctypes.create_string_buffer(68)
            size = wintypes.DWORD(68)
            if not advapi.CreateWellKnownSid(sid_type, None, sid, ctypes.byref(size)):
                continue
            name, domain = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
            n, d, use = wintypes.DWORD(256), wintypes.DWORD(256), wintypes.DWORD()
            if advapi.LookupAccountSidW(None, sid, name, ctypes.byref(n), domain, ctypes.byref(d), ctypes.byref(use)):
                names.add(name.value.lower())
                names.add((domain.value + "\\" + name.value).lower())
        except Exception:  # noqa: BLE001 - best effort; English names still work
            continue
    return names


def _console_encoding(platform: str | None = None) -> str:
    """icacls prints in the OEM code page (cp866 on Russian Windows), not UTF-8."""
    if (platform or os.name) != "nt":
        return "utf-8"
    try:
        import ctypes
        return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    except Exception:  # noqa: BLE001
        return "utf-8"


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    """Remove known secret values first (longest first), then anything that looks like one."""
    if not text:
        return text or ""
    for value in sorted({s for s in secrets if isinstance(s, str) and len(s) >= _MIN_SECRET_LEN}, key=len, reverse=True):
        text = text.replace(value, MARK)
    text = _TG_BOT.sub(MARK, text)
    text = _CODE_AFTER_WORD.sub(lambda m: f"{m.group(1)}{m.group(2)}{MARK}", text)
    text = _PHONE.sub(MARK, text)
    text = _HEX32.sub(MARK, text)
    text = _LONG_TOKEN.sub(MARK, text)
    return text


class RedactingFormatter(logging.Formatter):
    def __init__(self, secrets: Callable[[], Iterable[str]] = lambda: (), fmt: str | None = None):
        super().__init__(fmt or "%(asctime)s %(levelname)s %(name)s: %(message)s")
        self._secrets = secrets

    def format(self, record: logging.LogRecord) -> str:  # includes the traceback text
        return redact(super().format(record), self._secrets())


#: Everyone / BUILTIN\Users / Authenticated Users / INTERACTIVE, by well-known SID (language independent): they are never
#: allowed on a secret and are taken off explicitly, because `icacls /inheritance:r` only removes INHERITED entries.
_BROAD_SIDS = ("*S-1-1-0", "*S-1-5-32-545", "*S-1-5-11", "*S-1-5-4")


def restrict_to_owner(path: Path) -> bool:
    """Owner-only access that KEEPS the contents of a directory readable for the owner. True when applied.

    POSIX: chmod 0700 / 0600. Windows: ``icacls /inheritance:r /grant:r <user>:F`` — and for a DIRECTORY the grant is
    ``(OI)(CI)F``. The plain ``bcc.auth._restrict_to_owner`` grants the directory a NON-inheritable ACE: when the folder
    was not created by ``mkdir(mode=0o700)`` (a restored backup, a copied data folder, an older runtime) the files in it
    only hold ACEs INHERITED from it, Windows re-propagates the change, and every such file is left with an EMPTY DACL
    (``D:AI``, nobody can open it — not even our own worker, «Процесс звонков не запущен или упал»).
    With ``(OI)(CI)`` the owner's ACE is inherited by what is inside, so nothing is lost and new files get it too.
    """
    path = Path(path)
    if os.name != "nt":
        try:
            os.chmod(path, 0o700 if path.is_dir() else 0o600)
        except OSError:
            return False
        return True
    user = os.environ.get("USERNAME") or ""
    domain = os.environ.get("USERDOMAIN") or ""
    principal = f"{domain}\\{user}" if domain and user else (user or "%USERNAME%")
    grant = f"{principal}:(OI)(CI)F" if path.is_dir() else f"{principal}:F"
    try:
        proc = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", grant],  # noqa: S603
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=False)
        if proc.returncode != 0:
            warnings.warn(f"icacls did not restrict {path.name} (code {proc.returncode})", UserWarning, stacklevel=2)
            return False
        subprocess.run(["icacls", str(path), "/remove:g", *_BROAD_SIDS],  # noqa: S603 - best effort, exit code irrelevant
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=False)
    except Exception as exc:  # noqa: BLE001 - no icacls, a stripped image, a timeout: the doctor re-checks
        warnings.warn(f"could not run icacls for {path.name}: {type(exc).__name__}", UserWarning, stacklevel=2)
        return False
    return True


def repair_unreadable(paths: Iterable[Path]) -> list[str]:
    """Give the owner back files nobody can open (an EMPTY DACL left by an older build or a foreign tool): a cheap open()
    probe, no subprocess for healthy files. Returns the names that were repaired."""
    fixed: list[str] = []
    for path in paths:
        path = Path(path)
        if not path.is_file():
            continue
        try:
            with path.open("rb"):
                continue
        except PermissionError:
            if restrict_to_owner(path):
                fixed.append(path.name)
        except OSError:
            continue
    return fixed


def configure_worker_logging(home: Path, secrets: Callable[[], Iterable[str]] = lambda: (), *,
                             max_bytes: int = 512 * 1024, backups: int = 2) -> logging.Handler:
    """One redacting, size-capped, owner-only log file for the worker; third-party chatter pinned to WARNING."""
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = home / "worker.log"
    if not path.exists():
        fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    handler.setFormatter(RedactingFormatter(secrets))
    root = logging.getLogger()
    for old in [h for h in root.handlers if getattr(h, "_calls_worker", False)]:
        root.removeHandler(old)
    handler._calls_worker = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    for noisy in ("telethon", "pytgcalls", "ntgcalls", "aiohttp", "asyncio", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        restrict_to_owner(path)
    except Exception:  # noqa: BLE001 - best effort, the doctor re-checks
        pass
    return handler


# ---------------------------------------------------------------- ACL check

@dataclass
class AclReport:
    path: str
    ok: bool
    detail: str
    platform: str


def parse_icacls(output: str, path: str, current_user: str) -> tuple[bool, str]:
    """Return (ok, detail) from ``icacls <path>`` text. Pure function: fixture-testable on any OS."""
    principals: list[str] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.lower().startswith(("successfully processed", "processed")):
            continue
        if line.startswith(path):
            line = line[len(path):].strip()
        m = re.match(r"^(?P<who>[^:]+):\(", line)
        if m:
            principals.append(m.group("who").strip().lower())
    if not principals:
        return False, "no ACL entries parsed"
    user = current_user.lower()
    bad = [p for p in principals if any(p == d or p.endswith("\\" + d) for d in _WIN_DENIED)]
    allowed = _WIN_ALLOWED | _localized_allowed()
    extra = [p for p in principals if p not in allowed and p != user and not p.endswith("\\" + user.split("\\")[-1])]
    if bad:
        return False, "broad principal(s) present: " + ", ".join(sorted(set(bad)))
    if extra:
        return False, "unexpected principal(s): " + ", ".join(sorted(set(extra)))
    return True, "owner-only ACL"


def check_owner_only(path: Path) -> AclReport:
    path = Path(path)
    if not path.exists():
        return AclReport(str(path), True, "absent (nothing to protect yet)", os.name)
    if os.name != "nt":
        st = path.stat()
        if st.st_uid != os.getuid():
            return AclReport(str(path), False, "not owned by the current user", "posix")
        if st.st_mode & 0o077:
            return AclReport(str(path), False, f"group/other access bits set ({oct(st.st_mode & 0o777)})", "posix")
        return AclReport(str(path), True, "mode has no group/other bits", "posix")
    user = os.environ.get("USERDOMAIN", "") + "\\" + os.environ.get("USERNAME", "")
    try:
        proc = subprocess.run(["icacls", str(path)], capture_output=True, text=True, encoding=_console_encoding("nt"),  # noqa: S603
                              errors="replace", timeout=20, check=False)
    except Exception as exc:  # noqa: BLE001
        return AclReport(str(path), False, f"icacls unavailable ({type(exc).__name__})", "nt")
    ok, detail = parse_icacls(proc.stdout, str(path), user)
    return AclReport(str(path), ok and proc.returncode == 0, detail, "nt")


def check_calls_home(home: Path, secret_key: Path | None = None) -> list[AclReport]:
    """The files that matter: the calls directory, credentials.enc, config, state/history, the log, the Vault key."""
    names = ["credentials.enc", "config.json", "state.json", "history.jsonl", "worker.log", "STOP", "answering"]
    reports = [check_owner_only(home)] + [check_owner_only(home / n) for n in names]
    if secret_key is not None:
        reports.append(check_owner_only(secret_key))
    return reports
