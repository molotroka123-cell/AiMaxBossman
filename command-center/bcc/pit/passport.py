"""Jeff 1.5 versioned passport: evidence per fact, four layers that never mix.

Not a second store. Everything lives in the participant's existing namespace
(``pit-v1.7/personalities/<person_key>``) written through the vault's atomic
helpers:

- layer ``events``     conversation events   ``raw/events.jsonl`` (unchanged, consent gated)
- layer ``facts``      confirmed/inferred facts ``facts.jsonl`` (old rows stay readable; each row
                       gains an additive ``passport`` envelope, no old key is touched)
- layer ``style``      communication style   ``style.json`` (Master Parser narratives feed here)
- layer ``procedures`` verified Jeff procedures ``procedures.jsonl`` (only with verification evidence)

A fact envelope carries: source (message/voice/command + date), confidence, usage scope,
consent snapshot, freshness and a correction history (old values kept for the participant's
own view; forgetting a fact removes the whole row, history included).
"""
from __future__ import annotations

import copy
import json
import shutil
import time
from pathlib import Path
from typing import Any

JEFF_PASSPORT_SCHEMA = "jeff.passport/1"
LAYERS = ("events", "facts", "style", "procedures")
BACKUP_NAME = "facts.jsonl.pre-passport-1"
STYLE_FILE = "style.json"
PROCEDURES_FILE = "procedures.jsonl"
CONSENT_HISTORY_FILE = "consent_history.jsonl"
STALE_AFTER_DAYS = 180
_HISTORY_MAX = 20
_VALUE_MAX = 300


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _source_kind(row: dict[str, Any]) -> str:
    ref = str(row.get("source_message_id") or "")
    model = str(row.get("source_model") or "")
    if ref.startswith("command:") or model == "participant_command":
        return "command"
    if ref.startswith("voice:") or "voice" in ref.split(":")[0]:
        return "voice"
    if ref.startswith("master_parser") or model.startswith("master"):
        return "master_parser"
    return "message" if ref else "legacy"


def build_envelope(row: dict[str, Any], *, consent: Any | None = None, actor: str = "jeff",
                   action: str = "created") -> dict[str, Any]:
    """Derive a passport envelope from the fields a fact row already has (pure, lossless)."""
    date = str(row.get("observed_at") or row.get("first_seen") or row.get("ingested_at") or "")
    sensitive = str(row.get("sensitivity") or "") == "sensitive"
    envelope: dict[str, Any] = {
        "schema": JEFF_PASSPORT_SCHEMA, "layer": "facts", "version": 1,
        "source": {"kind": _source_kind(row), "ref": str(row.get("source_message_id") or ""),
                   "date": date},
        "confidence": float(row.get("confidence", 0.0) or 0.0),
        "evidence_kind": str(row.get("evidence_kind") or ""),
        "scope": "own_chat_local_only" if sensitive else "own_chat",
        "consent": {"basis": "memory_enabled", "sensitive": sensitive,
                    "version": getattr(consent, "version", "unknown") if consent is not None
                    else "legacy-unknown",
                    "accepted_at": getattr(consent, "accepted_at", None) if consent is not None
                    else None},
        "freshness": {"observed_at": date, "last_seen": str(row.get("last_seen") or date),
                      "valid_to": row.get("valid_to"), "ttl_seconds": row.get("ttl_seconds")},
        "history": [{"version": 1, "at": _now(), "actor": actor, "action": action}],
    }
    return envelope


def upgrade_row(row: dict[str, Any], *, consent: Any | None = None) -> dict[str, Any]:
    """Return a copy of ``row`` with a passport envelope; every original key is preserved."""
    out = copy.deepcopy(row)
    env = out.get("passport")
    if isinstance(env, dict) and env.get("schema") == JEFF_PASSPORT_SCHEMA:
        return out
    out["passport"] = build_envelope(row, consent=consent, actor="migration", action="migrated")
    return out


def freshness_status(row: dict[str, Any], *, now: float | None = None) -> str:
    """``fresh`` / ``stale`` / ``expired`` computed at read time from the stored dates."""
    now = time.time() if now is None else now
    fresh = (row.get("passport") or {}).get("freshness") or {}
    ttl = row.get("ttl_seconds") or fresh.get("ttl_seconds")
    stamp = str(fresh.get("last_seen") or fresh.get("observed_at") or "")
    try:
        seen = time.mktime(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except ValueError:
        return "unknown"
    age = now - seen
    if isinstance(ttl, (int, float)) and ttl > 0 and age > ttl:
        return "expired"
    return "stale" if age > STALE_AFTER_DAYS * 86400 else "fresh"


def apply_correction(row: dict[str, Any], old_value: Any, *, actor: str, surface: str = "",
                     action: str = "correct") -> None:
    """Bump the version and keep the replaced value in the row's own history (in place)."""
    env = row.get("passport")
    if not isinstance(env, dict) or env.get("schema") != JEFF_PASSPORT_SCHEMA:
        env = build_envelope(row, actor="migration", action="migrated")
        row["passport"] = env
    version = int(env.get("version", 1)) + 1
    env["version"] = version
    entry = {"version": version, "at": _now(), "actor": actor, "action": action,
             "previous_value": old_value if isinstance(old_value, (str, int, float, bool))
             or old_value is None else str(old_value)[:_VALUE_MAX]}
    if surface:
        entry["surface"] = str(surface)[:20]
    env["history"] = (list(env.get("history") or []) + [entry])[-_HISTORY_MAX:]
    env["confidence"] = float(row.get("confidence", env.get("confidence", 0.0)) or 0.0)
    env["evidence_kind"] = str(row.get("evidence_kind") or env.get("evidence_kind") or "")
    env.setdefault("freshness", {})["last_seen"] = _now()


# ----------------------------------------------------------------------------- migration
def migrate_person(vault: Any, person_key: str) -> dict[str, Any]:
    """Losslessly add envelopes to one participant's legacy fact rows (idempotent).

    A backup copy of the original file is kept once; the rewrite is verified (row count and every
    original key/value equal) and rolled back from the backup on any mismatch.
    """
    path = vault.person_dir(person_key) / "facts.jsonl"
    if not path.is_file():
        return {"migrated": 0, "already": 0, "ok": True}
    original_text = path.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in original_text.splitlines() if line.strip()]
    consent = vault.consent(person_key)
    upgraded, migrated = [], 0
    for row in rows:
        new = upgrade_row(row, consent=consent)
        if new is not row and "passport" not in row:
            migrated += 1
        upgraded.append(new)
    if not migrated:
        return {"migrated": 0, "already": len(rows), "ok": True}
    backup = path.with_name(BACKUP_NAME)
    if not backup.exists():
        shutil.copy2(path, backup)
    vault._rewrite_facts(person_key, upgraded)
    check = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    ok = len(check) == len(rows) and all(
        all(key in after and after[key] == value for key, value in before.items())
        for before, after in zip(rows, check))
    if not ok:
        shutil.copy2(backup, path)
        return {"migrated": 0, "already": 0, "ok": False, "error": "verification_failed_rolled_back"}
    vault.audit(person_key, "write", actor="jeff", fact_ids=[], categories=["passport_migration"])
    return {"migrated": migrated, "already": len(rows) - migrated, "ok": True}


def migrate_all(vault: Any) -> dict[str, Any]:
    """Migrate every participant namespace, each one independently (no cross-reads)."""
    total = {"people": 0, "migrated": 0, "failed": 0}
    for entry in sorted(vault.root.iterdir()) if vault.root.is_dir() else []:
        if not entry.is_dir():
            continue
        try:
            result = migrate_person(vault, entry.name)
        except (ValueError, OSError):
            total["failed"] += 1
            continue
        total["people"] += 1
        total["migrated"] += result["migrated"]
        total["failed"] += 0 if result["ok"] else 1
    return total


# ----------------------------------------------------------------------------- layers
def facts_layer(vault: Any, person_key: str) -> list[dict[str, Any]]:
    """Confirmed/inferred facts, envelope guaranteed (legacy rows upgraded in memory)."""
    consent = vault.consent(person_key)
    return [upgrade_row(row, consent=consent) for row in vault.iter_candidate_records(person_key)]


def events_layer_count(vault: Any, person_key: str) -> int:
    path = vault.person_dir(person_key) / "raw" / "events.jsonl"
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0


def read_style(vault: Any, person_key: str) -> dict[str, Any] | None:
    path = vault.person_dir(person_key) / STYLE_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("layer") == "style" else None


def write_style(person_dir: Path, narrative: dict[str, Any], *,
                run_id: str = "", model: str = "") -> bool:
    """Style layer from a Master Parser narrative. Requires memory consent; versioned.

    Style text is an inference
    and is labelled as such; it never becomes a fact and is never merged into ``facts.jsonl``.
    """
    from .vault import _atomic_json
    paragraphs = [str(p)[:450] for p in (narrative.get("paragraphs") or [])][:2]
    if not paragraphs or str(narrative.get("status", "OK")) != "OK":
        return False
    try:
        consent = json.loads((person_dir / "consent.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not (isinstance(consent, dict) and consent.get("memory_enabled")):
        return False
    path = person_dir / STYLE_FILE
    previous: dict[str, Any] = {}
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {}
    if previous.get("paragraphs") == paragraphs:
        return True
    version = int(previous.get("version", 0)) + 1
    history = list(previous.get("history") or [])
    if previous.get("paragraphs"):
        history.append({"version": previous.get("version", 1), "paragraphs": previous["paragraphs"],
                        "replaced_at": _now()})
    _atomic_json(path, {
        "schema": JEFF_PASSPORT_SCHEMA, "layer": "style", "version": version,
        "kind": "inference", "paragraphs": paragraphs,
        "source": {"kind": "master_parser", "run_id": str(run_id)[:80], "model": str(model)[:80],
                   "provenance": dict(narrative.get("provenance") or {})},
        "updated_at": _now(), "history": history[-_HISTORY_MAX:]})
    return True


def style_items(vault: Any, person_key: str) -> tuple[str, ...]:
    style = read_style(vault, person_key)
    if not style:
        return ()
    return tuple("Стиль общения (предположение, не факт): " + p for p in style["paragraphs"])


def add_procedure(vault: Any, person_key: str, *, name: str, steps: list[str],
                  verified_by: str, evidence_ref: str) -> bool:
    """A Jeff procedure enters the layer only with verification evidence (no evidence, no entry)."""
    from .vault import _append_jsonl
    if not (name.strip() and steps and verified_by.strip() and evidence_ref.strip()):
        return False
    if not vault.consent(person_key).memory_enabled:
        return False
    _append_jsonl(vault.ensure(person_key) / PROCEDURES_FILE, {
        "schema": JEFF_PASSPORT_SCHEMA, "layer": "procedures", "name": name.strip()[:80],
        "steps": [str(s)[:200] for s in steps][:20],
        "verification": {"by": verified_by.strip()[:80], "evidence": evidence_ref.strip()[:200],
                         "at": _now()}})
    return True


def procedures(vault: Any, person_key: str) -> list[dict[str, Any]]:
    path = vault.person_dir(person_key) / PROCEDURES_FILE
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return []


def layer_summary(vault: Any, person_key: str) -> dict[str, Any]:
    style = read_style(vault, person_key)
    return {"schema": JEFF_PASSPORT_SCHEMA,
            "events": events_layer_count(vault, person_key),
            "facts": len(facts_layer(vault, person_key)),
            "style": int(style["version"]) if style else 0,
            "procedures": len(procedures(vault, person_key))}


def note_consent_change(person_dir: Path, before: Any, after: Any) -> None:
    """Append changed consent flags (names and booleans only) to the participant's own log."""
    from dataclasses import asdict

    from .vault import _append_jsonl
    old, new = asdict(before), asdict(after)
    changes = {k: new[k] for k in new if k not in {"accepted_at", "version"} and old.get(k) != new[k]}
    if not changes:
        return
    try:
        _append_jsonl(person_dir / CONSENT_HISTORY_FILE, {"at": _now(), "changes": changes})
    except OSError:
        pass


def consent_history(vault: Any, person_key: str, last: int = 50) -> list[dict[str, Any]]:
    path = vault.person_dir(person_key) / CONSENT_HISTORY_FILE
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-max(1, last):]
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
