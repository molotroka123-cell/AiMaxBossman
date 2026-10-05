"""One resolver for the Telegram companion's home, plus the one-way migration into it.

Before 2026-10-01 the companion lived in ``%LOCALAPPDATA%\\Bossman\\telegram-companion``
no matter which Bossman instance (``BCC_DATA_DIR``) was talking to it, so a
test or release-candidate instance could rewrite the owner's real bot
configuration and profiles. The canonical home is now
``<data_dir>/telegram-companion`` (a sibling of ``pit-v1.7`` and
``telegram-calls``). An explicit ``BOSSMAN_TELEGRAM_CONFIG`` (or
``BOSSMAN_COMPANION_CONFIG``) still wins; it is how tests and operators pin a path.

The legacy folder is a migration SOURCE only. ``migrate_companion_home`` copies:
full timestamped backup first (sha256 + sqlite integrity), ``secret.key`` before
anything else (the sealed rows and ``credentials.enc`` are unreadable without
it), sqlite through the backup API, a temp folder and one atomic rename, then an
add-only ``MIGRATED_TO.json`` marker in the legacy home. The source is never
deleted, moved, truncated or rewritten.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path

HOME_NAME = "telegram-companion"
CONFIG_NAME = "config.json"
MARKER_NAME = "MIGRATED_TO.json"
ENV_OVERRIDES = ("BOSSMAN_TELEGRAM_CONFIG", "BOSSMAN_COMPANION_CONFIG")
#: Pollers and our own markers are state of a RUNNING process / of the migration, not data.
_SKIP_NAMES = {"poller.lock", "poller.json", MARKER_NAME}
_SKIP_SUFFIXES = (".tmp", "-journal", "-wal", "-shm")
_SECRET_FIRST = ("secret.key", "credentials.enc", CONFIG_NAME)

STATUS_MIGRATED = "MIGRATED"
STATUS_ALREADY = "ALREADY_MIGRATED"
STATUS_NO_LEGACY = "NO_LEGACY"
STATUS_IN_USE = "HOME_IN_USE"
STATUS_NO_KEY = "SECRET_KEY_MISSING"
STATUS_BACKUP_FAILED = "MIGRATION_BACKUP_FAILED"
STATUS_COPY_FAILED = "MIGRATION_COPY_FAILED"
STATUS_SAME_HOME = "SAME_HOME"


# ---------------------------------------------------------------- locations

def _local_base() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share")))


def legacy_home() -> Path:
    """The pre-2026-10-01 machine-global home. A migration source, never a target."""
    return _local_base() / "Bossman" / HOME_NAME


def legacy_config_path() -> Path:
    return legacy_home() / CONFIG_NAME


def companion_home(data_dir: Path | str | None = None) -> Path:
    if data_dir is None:
        from bcc.config import _data_dir
        data_dir = _data_dir()
    return Path(data_dir) / HOME_NAME


def env_override(order: tuple[str, ...] = ENV_OVERRIDES) -> Path | None:
    for name in order:
        value = os.environ.get(name, "").strip()
        if value:
            return Path(value)
    return None


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def legacy_applies(data_dir: Path | str | None = None) -> bool:
    """True only for the instance that sits NEXT to the legacy home (the owner's
    default data dir, ``%LOCALAPPDATA%\\Bossman\\CommandCenter``). A test, bugtest or
    candidate instance with its own ``BCC_DATA_DIR`` must never inherit the owner's
    bot token and profiles, so for it the legacy home is irrelevant."""
    base = companion_home(data_dir).parent.resolve()
    return _same(base, legacy_home().parent.resolve() / "CommandCenter")


def legacy_pending(data_dir: Path | str | None = None) -> bool:
    """Legacy config exists, applies to this instance, and nothing was migrated yet."""
    if env_override() is not None or not legacy_applies(data_dir):
        return False
    return legacy_config_path().is_file() and not (companion_home(data_dir) / CONFIG_NAME).is_file()


def companion_config_path(data_dir: Path | str | None = None, *,
                          env_order: tuple[str, ...] = ENV_OVERRIDES,
                          read_fallback: bool = False) -> Path:
    """The config.json every Telegram caller must use.

    1. an explicit environment override;
    2. ``<data_dir>/telegram-companion/config.json`` (``data_dir`` defaults to
       ``bcc.config._data_dir()``, i.e. ``BCC_DATA_DIR``).

    ``read_fallback`` is for READ-ONLY callers (owner reports, the market
    notifier, Jeff's bot guard): while the owner's instance has not migrated yet
    they may still read the legacy file instead of silently seeing "not
    configured". Writers never use it.
    """
    override = env_override(env_order)
    if override is not None:
        return override
    canonical = companion_home(data_dir) / CONFIG_NAME
    if read_fallback and not canonical.is_file() and legacy_pending(data_dir):
        return legacy_config_path()
    return canonical


def follow_marker(config: Path) -> Path:
    """A launcher still pointing at the legacy home follows ``MIGRATED_TO.json``."""
    config = Path(config)
    marker = config.parent / MARKER_NAME
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        target = Path(data["to"]) / CONFIG_NAME
    except (OSError, ValueError, KeyError, TypeError):
        return config
    if target.is_file() and not _same(target.parent, config.parent):
        return target
    return config


# ---------------------------------------------------------------- helpers

def _stamp(now: _dt.datetime | None) -> str:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return now.strftime("%Y%m%dT%H%M%SZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sqlite(path: Path) -> bool:
    return path.suffix == ".sqlite3"


def _copy_sqlite(source: Path, target: Path) -> None:
    """Consistent snapshot through sqlite's backup API; the source is opened read-only."""
    src = sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _integrity_ok(path: Path) -> bool:
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        return db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        db.close()


def _profile_rows(path: Path) -> dict[str, str]:
    """{who: sealed body} of the profiles table; read-only, {} when there is none."""
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        has = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='profiles'").fetchone()
        if not has:
            return {}
        return {r[0]: r[1] for r in db.execute("SELECT who, body FROM profiles")}
    finally:
        db.close()


def _files(root: Path) -> list[Path]:
    """Relative file paths worth copying: secret material first, then config, sqlite, the rest."""
    found: list[Path] = []
    for current, _dirs, names in os.walk(root):
        for name in names:
            rel = (Path(current) / name).relative_to(root)
            if name in _SKIP_NAMES or name.endswith(_SKIP_SUFFIXES):
                continue
            found.append(rel)

    def rank(rel: Path):
        if len(rel.parts) == 1 and rel.name in _SECRET_FIRST:
            return (0, _SECRET_FIRST.index(rel.name), str(rel))
        return (1 if _is_sqlite(rel) else 2, 0, str(rel))
    return sorted(found, key=rank)


def _copy_one(src_root: Path, dst_root: Path, rel: Path) -> None:
    source, target = src_root / rel, dst_root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if _is_sqlite(rel):
        _copy_sqlite(source, target)
    else:
        shutil.copy2(source, target)


def _result(status: str, **extra) -> dict:
    return {"status": status, "ok": status in {STATUS_MIGRATED, STATUS_ALREADY, STATUS_NO_LEGACY}, **extra}


def _hashes(root: Path, rels: list[Path]) -> dict[str, str]:
    return {rel.as_posix(): _sha256(root / rel) for rel in rels if not _is_sqlite(rel)}


# ---------------------------------------------------------------- migration

def needs_migration(data_dir: Path | str | None = None) -> bool:
    return legacy_pending(data_dir)


def ensure_migrated(data_dir: Path | str | None = None) -> dict | None:
    """Lazy migration for the owner's own instance only; ``None`` = nothing to do."""
    if not legacy_pending(data_dir):
        return None
    if data_dir is None:
        from bcc.config import _data_dir
        data_dir = _data_dir()
    return migrate_companion_home(legacy_home(), companion_home(data_dir), owner_data_dir=Path(data_dir))


def migrate_companion_home(legacy: Path, new: Path, *, now: _dt.datetime | None = None,
                           owner_data_dir: Path | None = None) -> dict:
    """Copy ``legacy`` into ``new``. Idempotent; never modifies or deletes ``legacy``."""
    legacy, new = Path(legacy), Path(new)
    if _same(legacy, new):
        return _result(STATUS_SAME_HOME)
    if (new / CONFIG_NAME).is_file():
        return _result(STATUS_ALREADY, to=str(new))
    if not (legacy / CONFIG_NAME).is_file():
        return _result(STATUS_NO_LEGACY)
    from .store import instance_holder
    if instance_holder(legacy) is not None:
        return _result(STATUS_IN_USE, reason="a companion holds the poller lock; stop it first")
    if not (legacy / "secret.key").is_file() and not os.environ.get("BOSSMAN_VAULT_KEY", "").strip():
        # A copy without the key would leave credentials and every sealed profile unreadable.
        return _result(STATUS_NO_KEY)

    stamp = _stamp(now)
    rels = _files(legacy)
    source_hashes = _hashes(legacy, rels)
    source_profiles = {rel.as_posix(): _profile_rows(legacy / rel) for rel in rels if _is_sqlite(rel)}

    # 1. FULL BACKUP, verified, before anything is written to the destination.
    backup = legacy.with_name(f"{legacy.name}.migration-backup-{stamp}")
    serial = 1
    while backup.exists():
        serial += 1
        backup = legacy.with_name(f"{legacy.name}.migration-backup-{stamp}-{serial}")
    try:
        backup.mkdir(mode=0o700, parents=True)      # holds secret.key and credentials.enc copies
        for rel in rels:
            _copy_one(legacy, backup, rel)
        if _hashes(backup, rels) != source_hashes:
            raise OSError("backup sha256 differs from the source")
        for rel in rels:
            if _is_sqlite(rel):
                if not _integrity_ok(backup / rel):
                    raise OSError("backup sqlite integrity check failed")
                if _profile_rows(backup / rel) != source_profiles[rel.as_posix()]:
                    raise OSError("backup profiles differ from the source")
    except Exception as exc:  # noqa: BLE001 — any failure must stop before the destination is touched
        shutil.rmtree(backup, ignore_errors=True)         # only our own partial backup
        return _result(STATUS_BACKUP_FAILED, reason=type(exc).__name__)

    # 2. Copy into a temp sibling, secret.key FIRST and verified, then the rest.
    tmp = new.with_name(f"{new.name}.tmp-{stamp}-{os.getpid()}")
    shutil.rmtree(tmp, ignore_errors=True)                 # a leftover of a crashed earlier attempt of OURS
    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        tmp.mkdir(mode=0o700)
        key_rel = Path("secret.key")
        for rel in rels:
            if rel == Path(CONFIG_NAME) and owner_data_dir is not None:
                _write_pinned_config(legacy / rel, tmp / rel, owner_data_dir)
            else:
                _copy_one(legacy, tmp, rel)
            if rel == key_rel and _sha256(tmp / rel) != source_hashes[rel.as_posix()]:
                raise OSError("secret.key copy does not match the source")
        _verify_destination(tmp, rels, source_hashes, source_profiles)
        if new.exists():
            # Nothing is deleted: whatever a half-started instance left there is parked.
            shutil.move(str(new), str(new.with_name(f"{new.name}.pre-migration-{stamp}")))
        os.replace(tmp, new)
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(tmp, ignore_errors=True)             # ONLY the temp folder; legacy is untouched
        return _result(STATUS_COPY_FAILED, reason=type(exc).__name__, backup=str(backup))

    after = {rel.as_posix(): len(_profile_rows(new / rel)) for rel in rels if _is_sqlite(rel)}
    info = {"to": str(new), "at": _dt.datetime.now(_dt.timezone.utc).isoformat(), "backup": str(backup),
            "sha256": source_hashes}
    marker_error = ""
    try:
        _write_marker(legacy, info)
    except OSError as exc:
        marker_error = type(exc).__name__
    return _result(STATUS_MIGRATED, to=str(new), backup=str(backup), sha256=source_hashes,
                   profiles_before={k: len(v) for k, v in source_profiles.items()},
                   profiles_after=after, marker_error=marker_error)


def _write_pinned_config(source: Path, target: Path, data_dir: Path) -> None:
    """Byte copy, except an empty ``core_data_dir`` is pinned to the owning data dir in the COPY."""
    raw = source.read_bytes()
    try:
        cfg = json.loads(raw)
    except ValueError:
        cfg = None
    if isinstance(cfg, dict) and not cfg.get("core_data_dir"):
        cfg["core_data_dir"] = str(Path(data_dir).resolve())
        raw = (json.dumps(cfg, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)


def _verify_destination(tmp: Path, rels: list[Path], source_hashes: dict[str, str],
                        source_profiles: dict[str, dict[str, str]]) -> None:
    for rel in rels:
        name = rel.as_posix()
        if _is_sqlite(rel):
            if not _integrity_ok(tmp / rel):
                raise OSError("copied sqlite failed its integrity check")
            if _profile_rows(tmp / rel) != source_profiles[name]:
                raise OSError("copied profiles differ from the source")
        elif name != CONFIG_NAME and _sha256(tmp / rel) != source_hashes[name]:
            raise OSError(f"{name} copy does not match the source")
    # The proof that matters: the copied key opens the copied secrets.
    from bcc.secrets import Vault
    vault = Vault(tmp)
    credentials = tmp / "credentials.enc"
    if credentials.is_file() and vault.decrypt(credentials.read_text(encoding="utf-8")) is None:
        raise OSError("copied secret.key cannot decrypt the copied credentials")
    for rel in rels:
        if _is_sqlite(rel):
            for body in _profile_rows(tmp / rel).values():
                if vault.decrypt(body) is None:
                    raise OSError("copied secret.key cannot decrypt a copied profile")


def _write_marker(legacy: Path, info: dict) -> None:
    """Add-only: an existing marker is never overwritten."""
    name = MARKER_NAME
    path = legacy / name
    serial = 1
    while path.exists():
        serial += 1
        path = legacy / f"MIGRATED_TO-{serial}.json"
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(json.dumps(info, ensure_ascii=False, indent=2) + "\n")


def migrate_to_data_dir(data_dir: Path | str | None = None) -> dict:
    """Explicit CLI migration: legacy home -> ``<data_dir>/telegram-companion`` for ANY instance."""
    if data_dir is None:
        from bcc.config import _data_dir
        data_dir = _data_dir()
    return migrate_companion_home(legacy_home(), companion_home(data_dir), owner_data_dir=Path(data_dir))
