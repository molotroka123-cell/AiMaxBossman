"""Keep the two Telegram bots apart: Jeff (PIT) and the owner's «Пульт» companion.

- A per-TOKEN kernel lock: only one poller per bot token on this machine, even
  across different data dirs (the per-data-dir ``poller.lock`` cannot see a
  second PIT started with another ``--data-dir`` but the same token).
- Jeff refuses to start with the companion bot's token: owner approvals,
  STOP, Computer Use and coding live only on the companion bot.

Only a SHA-256 fingerprint of a token is ever used in a path or compared.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
from pathlib import Path

from bcc.telegram_companion.config import CompanionError


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()[:24]


def _lock_dir() -> Path:
    configured = os.environ.get("BOSSMAN_TELEGRAM_POLLER_LOCK_DIR", "").strip()
    if configured:
        return Path(configured)
    base = os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share"))
    return Path(base) / "Bossman" / "telegram-pollers"


@contextlib.contextmanager
def token_poller_lock(token: str):
    """Kernel-owned lock keyed by the token fingerprint; released on process death."""
    if not token:
        raise CompanionError("TELEGRAM_NOT_CONFIGURED")
    directory = _lock_dir()
    directory.mkdir(parents=True, exist_ok=True)
    file = (directory / f"{token_fingerprint(token)}.lock").open("a+b")
    try:
        if file.seek(0, 2) == 0:
            file.write(b"0")
            file.flush()
        file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise CompanionError("ANOTHER_POLLER_FOR_THIS_BOT_TOKEN") from None
        try:
            yield
        finally:
            release_byte_lock(file)
    finally:
        file.close()


def release_byte_lock(file) -> None:
    """Unlock byte 0 BEFORE closing: Windows frees a lock left on a closed
    handle only "when system resources allow", so an immediate restart could
    still be refused as a second poller."""
    try:
        file.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(file, fcntl.LOCK_UN)
    except OSError:
        pass


def companion_config_path(data_dir: Path | None = None) -> Path:
    """Same resolver as the companion itself (BOSSMAN_COMPANION_CONFIG keeps priority here)."""
    from bcc.telegram_companion.paths import companion_config_path as resolve
    return resolve(data_dir, env_order=("BOSSMAN_COMPANION_CONFIG", "BOSSMAN_TELEGRAM_CONFIG"), read_fallback=True)


def companion_token_fingerprint(config: Path | None = None, data_dir: Path | None = None) -> str | None:
    """Fingerprint of the companion («Пульт») bot token, or None when unknown."""
    path = Path(config) if config else companion_config_path(data_dir)
    if not path.is_file():
        return None
    try:
        from bcc.telegram_companion.config import load
        settings = load(path, env_file=path.parent / "companion.env")
    except Exception:  # noqa: BLE001 — a broken companion config is not Jeff's to judge
        return None
    token = getattr(settings, "bot_token", "") or ""
    return token_fingerprint(token) if token else None


def assert_not_companion_bot(pit_token: str, companion_config: Path | None = None,
                             data_dir: Path | None = None) -> None:
    fingerprint = companion_token_fingerprint(companion_config, data_dir)
    if fingerprint and fingerprint == token_fingerprint(pit_token):
        raise CompanionError("PIT_TOKEN_IS_THE_OWNER_COMPANION_BOT")
