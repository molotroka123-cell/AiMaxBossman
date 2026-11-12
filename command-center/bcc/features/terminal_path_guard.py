"""Path guard for ``terminal.run`` (architecture P0 step 1, 12.11.2026).

Why: ``filesystem.read_any`` refuses chat DBs, browser profiles, DPAPI/keys/.env/vault, Grechka ``*.enc``, user media,
wallets and registry hives through ``fs_read_policy.check_path``, but ``terminal.run`` had only a regex ``HARD_DENY``
that covers part of that and none of the browser/chat/media/mail/Grechka classes. ``type``/``Get-Content``/``copy``
read everything the user account can read.

What this does: extract the file/dir arguments of a shell command (read verbs, redirects, quoted strings, ``-c`` /
``-Command`` payloads, ``sqlite3 <db>``, archive/copy sources, wildcard expansion), expand ``%VAR%`` / ``$env:VAR`` /
``$VAR`` / ``~``, resolve relative paths against the call's cwd, and run ``fs_read_policy.check_path`` on each (that call
also follows symlinks and junctions). A hit returns a reason string that starts with the policy code (``FS_DENY_*``).

HONEST LIMIT: this is best-effort defence in depth, NOT a sandbox. A command that builds the path at run time
(string concatenation, ``[char]`` codes, base64, a script file that opens the path, environment tricks, a ``for /r``
walk) can evade it. The real boundary is the existing HARD_DENY list and the hook floors (unchanged) and, in P2, running
the operator shell under a separate Windows user / Job Object without access to the profile directories.

The guard can only tighten: it is consulted by the effect hook (-> DENY) and again at the effect boundary. Switch it
off with ``BCC_TERMINAL_PATH_GUARD=0`` (rollback flag; default on).
"""
from __future__ import annotations

import glob
import os
import re
import shlex

from .fs_read_policy import check_path

_ENV_FLAG = "BCC_TERMINAL_PATH_GUARD"
_MAX_GLOB = 50
_MAX_DEPTH = 3

# verbs whose non-option arguments are files/dirs even when written bare (no separator)
_READ_VERBS = frozenset({
    "type", "cat", "more", "less", "head", "tail", "get-content", "gc", "copy", "xcopy", "robocopy", "copy-item",
    "cp", "move", "mv", "move-item", "findstr", "select-string", "sls", "grep", "egrep", "rg", "sqlite3", "strings",
    "compress-archive", "tar", "7z", "7za", "zip", "certutil", "get-childitem", "gci", "dir", "ls", "tree",
    "get-item", "gi", "get-itemproperty", "invoke-item", "ii", "import-csv", "convertfrom-json", "notepad",
    "start", "xxd", "hexdump", "od", "base64", "get-filehash", "certutil.exe", "expand", "makecab", "attrib",
    "icacls", "takeown", "ren", "rename", "rename-item", "get-acl", "format-hex", "sha256sum", "md5sum", "file",
    "stat", "wc", "sort", "diff", "fc", "comp", "wmic", "reg", "esentutl", "vssadmin", "ntdsutil", "rclone",
})
_SHELLS = frozenset({"cmd", "powershell", "pwsh", "bash", "sh", "wsl", "python", "python3", "py", "node", "perl",
                     "ruby", "php", "cscript", "wscript"})
_SEG_SPLIT = re.compile(r"\|\||&&|[;&|\r\n]")
_DRIVE_CAPTURE = re.compile(r"""[A-Za-z]:[\\/][^"'<>|\r\n;&]*""")
_ENV_PS = re.compile(r"\$\{?env:([A-Za-z_][A-Za-z0-9_]*)\}?", re.I)
_ENV_SH = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")
_URL = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)


def enabled() -> bool:
    return os.environ.get(_ENV_FLAG, "1").strip().lower() not in ("0", "false", "off", "no")


def _expand(tok: str) -> str:
    def ps(m: re.Match) -> str:
        return os.environ.get(m.group(1), m.group(0))

    def sh(m: re.Match) -> str:
        return os.environ.get(m.group(1), m.group(0))

    t = _ENV_PS.sub(ps, tok)
    t = _ENV_SH.sub(sh, t) if "$" in t else t
    t = os.path.expandvars(t)             # %VAR% on Windows
    if t == "~" or t.startswith(("~/", "~\\")):
        t = os.path.expanduser(t)
    return t


def _pathlike(tok: str) -> bool:
    if not tok or _URL.match(tok):
        return False
    if re.match(r"^[A-Za-z]:", tok) or tok.startswith("~") or "%" in tok or "$" in tok:
        return True
    if "\\" in tok:
        return True
    if "/" in tok:
        # «/c», «/s», «/E» are cmd switches, not paths
        return not re.fullmatch(r"/[A-Za-z?]{1,2}", tok) and not re.fullmatch(r"/[A-Za-z?]+:?[^/\\]*", tok)
    return False


def _split_args(text: str) -> list[str]:
    try:
        toks = shlex.split(text, posix=False)
    except ValueError:
        toks = text.split()
    out = []
    for t in toks:
        t = t.strip()
        if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
            t = t[1:-1]
        t = t.lstrip("<>")
        t = re.sub(r"^\d>+&?", "", t)
        if t:
            out.append(t)
    return out


def _verb(tok: str) -> str:
    base = re.split(r"[\\/]", tok)[-1].lower()
    return base[:-4] if base.endswith(".exe") else base


def _flag_value(tok: str) -> str | None:
    """``-Path:C:\\x`` / ``--file=C:\\x`` -> value part."""
    m = re.match(r"^--?[A-Za-z][\w-]*[:=](.+)$", tok)
    return m.group(1).strip("\"'") if m else None


def _candidates(command: str, depth: int = 0) -> list[tuple[str, bool]]:
    """(token, bare_ok): ``bare_ok`` = the token is an argument of a read verb, so a bare name is a path too."""
    found: list[tuple[str, bool]] = []
    for m in _DRIVE_CAPTURE.finditer(command):
        found.append((m.group(0).rstrip(), False))
    for seg in _SEG_SPLIT.split(command):
        toks = _split_args(seg)
        if not toks:
            continue
        # skip leading call operator / env assignments
        while toks and (toks[0] in ("&", ".") or re.match(r"^[A-Za-z_]\w*=", toks[0])):
            toks = toks[1:]
        if not toks:
            continue
        verb = _verb(toks[0])
        is_read = verb in _READ_VERBS
        for t in toks[1:]:
            val = _flag_value(t)
            if val is not None:
                t = val
            elif t.startswith("-") and not _pathlike(t.lstrip("-")) or (t.startswith("-") and len(t) <= 24
                                                                       and "\\" not in t and "/" not in t):
                if verb in _SHELLS or t.startswith("-"):
                    continue
            if depth < _MAX_DEPTH and (verb in _SHELLS or verb in ("start", "invoke-expression", "iex")) and \
                    (" " in t or ";" in t or "(" in t) and not _pathlike(t.split()[0] if t.split() else t):
                found.extend(_candidates(t, depth + 1))        # -c / -Command payload
            if _pathlike(t):
                found.append((t, is_read))
            elif is_read and t and not t.startswith("-") and not t.startswith("/"):
                found.append((t, True))
            # python-ish payload with embedded literals: pick up path-like substrings
            if depth < _MAX_DEPTH and ("\\" in t or "/" in t) and len(t) > 3:
                for m in re.finditer(r"""[A-Za-z0-9_.$%~:{}()\\/ -]*[\\/][A-Za-z0-9_.$%~:{}()\\/ -]*""", t):
                    s = m.group(0).strip()
                    if _pathlike(s):
                        found.append((s, False))
    return found


def _resolve(tok: str, cwd: str | None) -> str:
    t = _expand(tok).strip().strip("\"'")
    if re.match(r"^[A-Za-z]:[\\/]", t) or t.startswith("\\\\"):
        return t
    if t.startswith(("\\", "/")) and not t.startswith("\\\\"):
        drive = os.path.splitdrive(cwd or os.getcwd())[0] or "C:"
        return drive + t
    base = cwd or "C:\\__cwd__"
    return os.path.join(base, t)


def path_guard_reason(command: str, cwd: str | None = None) -> str:
    """Return ``"FS_DENY_xxx: <why> (<path>)"`` when the command touches a denied class, else ``""``."""
    if not enabled():
        return ""
    cmd = str(command or "")
    if not cmd.strip():
        return ""
    seen: set[str] = set()
    for tok, bare_ok in _candidates(cmd):
        if not (bare_ok or _pathlike(tok)):
            continue
        full = _resolve(tok, cwd)
        targets = [full]
        if any(ch in full for ch in "*?["):
            try:
                targets += glob.glob(full)[:_MAX_GLOB]
            except (OSError, re.error):
                pass
        for target in targets:
            key = target.lower()
            if key in seen:
                continue
            seen.add(key)
            try:
                verdict = check_path(target)
            except Exception:  # noqa: BLE001 - a policy crash must not silently allow
                continue
            if not verdict.ok and verdict.code.startswith("FS_DENY"):
                shown = tok if len(tok) <= 80 else tok[:77] + "..."
                return f"{verdict.code}: {verdict.why} ({shown})"
    return ""
