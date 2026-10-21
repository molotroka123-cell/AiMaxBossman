"""Paths and defaults for doc_intake. Nothing here touches the network.

The data directory is resolved the way the LIVE backend sees it: ``BCC_DATA_DIR``
when set, otherwise ``%LOCALAPPDATA%\\Bossman\\CommandCenter``. ``bcc.config``
is deliberately NOT used: for a source checkout without ``BCC_DATA_DIR`` it
resolves to ``<repo>/command-center/data`` (the known BCC_DATA_DIR trap), which
is the wrong token and the wrong inbox for the owner's running Bossman.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_MAX_BYTES = 200 * 1024 * 1024
DEFAULT_BACKEND = "http://127.0.0.1:8801"
DEFAULT_WRITE_FOLDER = "BOSSMAN Memory"
#: Sub-folder of the vault write folder that holds library notes.
VAULT_LIBRARY_FOLDER = "Library"

ENV_DATA_DIR = "BCC_DATA_DIR"
ENV_LIBRARY = "BOSSMAN_LIBRARY_ROOT"
ENV_INBOX = "BOSSMAN_DOC_INBOX"
ENV_VAULT = "BOSSMAN_MEMORY_VAULT"
ENV_BACKEND = "BOSSMAN_BACKEND"


def ensure_repo_imports() -> None:
    """Make the repo's own packages importable (bcc secret filter, file_intel parsers).

    Appended, never prepended: an installed package must not be shadowed.
    """
    for sub in ("command-center", "bossman-core"):
        path = REPO_ROOT / sub
        if path.is_dir() and str(path) not in sys.path:
            sys.path.append(str(path))


def default_data_dir(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    configured = (env.get(ENV_DATA_DIR) or "").strip()
    if configured:
        return Path(configured).expanduser()
    base = env.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Bossman" / "CommandCenter"


def default_library_root(data_dir: Path, env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    configured = (env.get(ENV_LIBRARY) or "").strip()
    return Path(configured).expanduser() if configured else Path(data_dir) / "library"


def default_inbox(data_dir: Path, env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    configured = (env.get(ENV_INBOX) or "").strip()
    return Path(configured).expanduser() if configured else Path(data_dir) / "inbox-docs"


def lower_priority() -> bool:
    """Best effort: run below normal priority so photo/video generation keeps the CPU."""
    try:
        if sys.platform == "win32":
            import ctypes

            below_normal = 0x00004000
            kernel32 = ctypes.windll.kernel32
            return bool(kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), below_normal))
        os.nice(10)
        return True
    except (OSError, AttributeError, ValueError):
        return False
