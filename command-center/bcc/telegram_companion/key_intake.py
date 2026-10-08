"""Owner provider-key intake from Telegram («Пульт»), owner request 07.10.

The owner sends one explicit command in the private owner chat:

    /key OPENROUTER_API_KEY=<value>
    /key NVIDIA_API_KEY=<value>        (several NAME=value lines are allowed)

The value goes ONLY into the owner's local key file (the one coding_tasks reads first,
%LOCALAPPDATA%\\Bossman\\keys\\provider-keys.env), written atomically with an owner-only ACL.
The message never enters Store/SQLite, chat history, learning logs or any model; the Telegram
message is deleted and the reply names the keys only. tools/keys_guard.py keeps the DPAPI copy
(the one-Bossman launcher runs `ensure` before the backend starts).

Anything that is not exactly `/key NAME=value` lines is refused without being stored.
"""
from __future__ import annotations

import contextlib
import os
import re
import subprocess
import tempfile
from pathlib import Path

COMMAND = "/key"
_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
MAX_VALUE = 512
MAX_LINES = 8


class KeyIntakeError(ValueError):
    """A refused /key message. The text is safe to show: it never contains a value."""


def keys_file() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Bossman" / "keys" / "provider-keys.env"


def is_key_command(text: str) -> bool:
    head = str(text or "").lstrip().split(None, 1)
    return bool(head) and head[0].split("@", 1)[0].lower() == COMMAND


def parse_key_command(text: str) -> dict[str, str]:
    """`/key NAME=value[\\nNAME=value...]` -> {NAME: value}. Never echoes a value in errors."""
    body = str(text or "").lstrip()
    if not is_key_command(body):
        raise KeyIntakeError("KEY_COMMAND_EXPECTED")
    rest = body.split(None, 1)[1] if len(body.split(None, 1)) > 1 else ""
    lines = [ln.strip() for ln in rest.splitlines() if ln.strip()]
    if not lines:
        raise KeyIntakeError("формат: /key ИМЯ=значение")
    if len(lines) > MAX_LINES:
        raise KeyIntakeError(f"не больше {MAX_LINES} ключей за раз")
    out: dict[str, str] = {}
    for n, line in enumerate(lines, 1):
        name, sep, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if not sep or not _NAME_RE.fullmatch(name):
            raise KeyIntakeError(f"строка {n}: имя должно быть вида OPENROUTER_API_KEY")
        if not value or len(value) > MAX_VALUE or any(c.isspace() for c in value):
            raise KeyIntakeError(f"строка {n} ({name}): пустое, слишком длинное или с пробелами значение")
        if name in out:
            raise KeyIntakeError(f"{name} указан дважды")
        out[name] = value
    return out


def _parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if k.strip():
                out[k.strip()] = v.strip()
    return out


def _owner_only(path: Path) -> None:
    if os.name != "nt":
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
        return
    user = os.environ.get("USERNAME")
    if user:
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                       capture_output=True, check=False)


def store_keys(keys: dict[str, str], path: Path | None = None) -> dict:
    """Merge into the local key file atomically. Returns names only."""
    path = path or keys_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    current = _parse_env(path.read_text(encoding="utf-8-sig", errors="replace")) if path.is_file() else {}
    added = sorted(k for k in keys if k not in current)
    replaced = sorted(k for k in keys if k in current and current[k] != keys[k])
    merged = {**current, **keys}
    data = "".join(f"{k}={merged[k]}\n" for k in sorted(merged)).encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    _owner_only(path)
    return {"added": added, "replaced": replaced, "names": sorted(merged)}
