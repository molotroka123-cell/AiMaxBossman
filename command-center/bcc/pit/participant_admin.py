"""Owner-side operations on ONE participant's stored data (Jeff Admin, Bossman 1.9).

Used by the existing Jeff settings API (``bcc.features.jeff_settings``). Every
function takes one person key and touches only that person's namespace under
``pit-v1.7/personalities/<key>`` (via ``PersonaVault``) and that person's
profile file; nothing here iterates other people's facts, so memory can never
be mixed between users. Audit rows and owner events never store fact values.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from . import participant_profile as pp
from .config import pit_home
from .identity import validate_person_key
from .models import ConsentState
from .secret_filter import redact_secrets
from .vault import PersonaVault, _append_jsonl

EVENTS_FILE = "admin_events.jsonl"
CLEAR_SCOPES = ("facts", "history", "summaries", "derived", "all")
_SUB = {"history": "raw", "summaries": "summaries", "derived": "derived"}
_VALUE_MAX = 300


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def log_event(data_dir: Path, person_key: str, action: str, **detail: Any) -> None:
    """Owner action log: who was touched and what kind of action, never any content."""
    path = pp.profile_dir(data_dir) / EVENTS_FILE
    row = {"at": _now(), "action": action, "person": validate_person_key(person_key)[:8]}
    row.update({k: v for k, v in detail.items() if isinstance(v, (str, int, bool))})
    try:
        _append_jsonl(path, row)
    except OSError:
        pass


def recent_owner_events(data_dir: Path, last: int = 30) -> list[dict]:
    path = pp.profile_dir(data_dir) / EVENTS_FILE
    if not path.is_file():
        return []
    rows: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-max(1, last):]
    except OSError:
        return []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _count_lines(path: Path) -> int:
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0


def stored_context(vault: PersonaVault, person_key: str) -> dict[str, Any]:
    """What Jeff has stored about this one person: facts (confirmed flagged), counts, summaries."""
    key = validate_person_key(person_key)
    target = vault.person_dir(key)
    exists = target.is_dir()
    facts = []
    for row in vault.list_facts(key) if exists else []:
        value = row.get("value")
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        text, _ = redact_secrets(str(text))
        facts.append({"id": row["id"], "category": row["category"], "key": row["key"],
                      "value": text[:_VALUE_MAX], "evidence_kind": row["evidence_kind"],
                      "confirmed": row["evidence_kind"] == "confirmed"})
    summaries = []
    if exists and (target / "summaries").is_dir():
        for path in sorted((target / "summaries").iterdir())[:10]:
            if path.is_file():
                try:
                    body = path.read_text(encoding="utf-8")[:400]
                except (OSError, UnicodeDecodeError):
                    body = ""
                summaries.append({"name": path.name, "text": redact_secrets(body)[0]})
    consent: ConsentState = vault.consent(key) if exists else ConsentState()
    return {
        "exists": exists,
        "facts": facts,
        "confirmed_facts": sum(1 for f in facts if f["confirmed"]),
        "raw_events": _count_lines(target / "raw" / "events.jsonl") if exists else 0,
        "summaries": summaries,
        "consent": {"memory": consent.memory_enabled, "raw_history": consent.raw_history_enabled,
                    "sensitive": consent.sensitive_memory_enabled,
                    "cloud_personalization": consent.remote_personalization_enabled},
        "audit": vault.memory_audit(key, last=25) if exists else [],
    }


def clear(vault: PersonaVault, person_key: str, scope: str) -> dict[str, Any]:
    """Erase one part of ONE participant's stored data; other people are never visited."""
    key = validate_person_key(person_key)
    if scope not in CLEAR_SCOPES:
        raise ValueError("unknown clear scope")
    target = vault.person_dir(key)
    removed = False
    if scope == "all":
        removed = vault.delete(key)
    elif target.is_dir():
        if scope == "facts":
            removed = (target / "facts.jsonl").is_file()
            (target / "facts.jsonl").unlink(missing_ok=True)
        else:
            folder = target / _SUB[scope]
            removed = folder.is_dir() and any(folder.iterdir())
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(parents=True, exist_ok=True)
        vault.audit(key, "delete_all", actor="owner", surface="admin")
    log_event(vault.data_dir, key, "clear", scope=scope, removed=removed)
    return {"scope": scope, "removed": removed}


def delete_fact(vault: PersonaVault, person_key: str, fact_id: str) -> bool:
    ok = vault.delete_fact(validate_person_key(person_key), fact_id, actor="owner", surface="admin")
    if ok:
        log_event(vault.data_dir, person_key, "fact_delete")
    return ok


def pause_memory(vault: PersonaVault, person_key: str) -> bool:
    """The owner may only RESTRICT memory; turning it on stays the participant's consent."""
    key = validate_person_key(person_key)
    if not vault.person_dir(key).is_dir():
        return False
    state = vault.consent(key)
    state.memory_enabled = False
    state.raw_history_enabled = False
    vault.set_consent(key, state)
    log_event(vault.data_dir, key, "memory_paused")
    return True


def forget_chat_history(data_dir: Path, jeff_dir: Path, cfg: dict, salt: bytes | None,
                        person_key: str) -> int:
    """Best effort: drop the short-term conversation window kept in the sqlite stores."""
    if not salt:
        return 0
    from bcc.telegram_companion.store import Store
    from .identity import derive_person_key
    from .web import derive_web_person_key
    key = validate_person_key(person_key)
    cleared = 0
    candidates: list[tuple[Path, str]] = []
    for person in cfg.get("people") or []:
        try:
            uid = int(person["user_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if derive_person_key(uid, salt) == key:
            candidates.append((pit_home(jeff_dir), f"{uid}:{uid}"))
    try:
        accounts = json.loads((pit_home(jeff_dir) / "web" / "accounts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        accounts = {}
    for row in (accounts.get("users") or {}).values():
        try:
            uid = int(row["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if derive_web_person_key(uid, salt) == key:
            candidates.append((pit_home(jeff_dir) / "web", f"{uid}:{uid}"))
    for home, who in candidates:
        if not (home / "companion.sqlite3").is_file():
            continue
        try:
            store = Store(home)
            try:
                store.forget(who)
                cleared += 1
            finally:
                store.close()
        except Exception:  # noqa: BLE001 — reported as not cleared, never fatal
            continue
    return cleared


def isolation_report(vault: PersonaVault) -> dict[str, Any]:
    """Structural check that every person namespace is separate and inside the PIT root.

    It reads directory names only, never another person's facts.
    """
    root = vault.root
    problems: list[str] = []
    people = 0
    seen: set[Path] = set()
    try:
        entries = sorted(root.iterdir())
    except OSError:
        entries = []
    for entry in entries:
        if not entry.is_dir() and not entry.is_symlink():
            continue
        people += 1
        try:
            validate_person_key(entry.name)
        except ValueError:
            problems.append("namespace name is not a person key")
            continue
        if entry.is_symlink() or (hasattr(entry, "is_junction") and entry.is_junction()):
            problems.append(f"{entry.name[:6]}: namespace is a link")
            continue
        resolved = entry.resolve()
        if resolved.parent != root.resolve() or resolved in seen:
            problems.append(f"{entry.name[:6]}: namespace escapes the PIT root or is shared")
        seen.add(resolved)
    return {"policy": "strict", "locked": True, "namespaces": people, "ok": not problems,
            "problems": problems[:20],
            "statement": "Память каждого участника хранится отдельно и никогда не смешивается с чужой; "
                         "эту защиту нельзя отключить."}


def profile_view(data_dir: Path, person_key: str) -> dict[str, Any]:
    profile, error = pp.read_profile(data_dir, person_key)
    return {"profile": profile, "error": error}

