from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from .identity import derive_person_key, scoped_person_dir
from .models import ConsentState, MemoryCandidate
from .secret_filter import redact_secrets


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


class PersonaVault:
    """Per-user namespace under the existing Bossman data_dir.

    This is not a second backend or task engine. It is a namespaced evidence
    spool/persona view that will later connect to Bossman's canonical memory and
    derived indexes.
    """

    def __init__(self, data_dir: Path, identity_salt: bytes):
        self.data_dir = Path(data_dir).resolve()
        # PIT data is physically separated from every other Bossman memory.
        self.root = self.data_dir / "pit-v1.7" / "personalities"
        self.root.mkdir(parents=True, exist_ok=True)
        if len(identity_salt) < 16:
            raise ValueError("identity_salt must be at least 16 bytes")
        self.identity_salt = bytes(identity_salt)

    def key_for_telegram(self, telegram_user_id: int | str) -> str:
        return derive_person_key(telegram_user_id, self.identity_salt)

    def person_dir(self, person_key: str) -> Path:
        return scoped_person_dir(self.root, person_key)

    def ensure(self, person_key: str) -> Path:
        target = self.person_dir(person_key)
        for name in ("raw", "summaries", "derived", "security"):
            (target / name).mkdir(parents=True, exist_ok=True)
        return target

    def consent(self, person_key: str) -> ConsentState:
        path = self.person_dir(person_key) / "consent.json"
        if not path.is_file():
            return ConsentState()
        data = json.loads(path.read_text(encoding="utf-8"))
        return ConsentState(**{k: data[k] for k in asdict(ConsentState()) if k in data})

    def set_consent(self, person_key: str, state: ConsentState) -> None:
        target = self.ensure(person_key)
        _atomic_json(target / "consent.json", state.to_dict())

    def append_raw_event(self, person_key: str, event: dict[str, Any]) -> bool:
        state = self.consent(person_key)
        if not (state.memory_enabled and state.raw_history_enabled):
            return False
        target = self.ensure(person_key)
        clean = dict(event)
        if "text" in clean:
            clean["text"], clean["secret_redacted"] = redact_secrets(str(clean["text"]))
        _append_jsonl(target / "raw" / "events.jsonl", clean)
        return True

    def append_candidate(self, person_key: str, candidate: MemoryCandidate) -> bool:
        state = self.consent(person_key)
        if not candidate.durable_allowed(state):
            return False
        target = self.ensure(person_key)
        payload = candidate.to_dict()
        if isinstance(payload.get("value"), str):
            payload["value"], redacted = redact_secrets(payload["value"])
            if redacted:
                return False
        _append_jsonl(target / "facts.jsonl", payload)
        return True

    def append_question(self, person_key: str, payload: dict[str, Any]) -> None:
        _append_jsonl(self.ensure(person_key) / "questions.jsonl", dict(payload))

    def append_correction(self, person_key: str, payload: dict[str, Any]) -> None:
        _append_jsonl(self.ensure(person_key) / "corrections.jsonl", dict(payload))

    def write_profile(self, person_key: str, payload: dict[str, Any]) -> None:
        _atomic_json(self.ensure(person_key) / "profile.json", dict(payload))

    def roleplay_state(self, person_key: str) -> dict[str, Any]:
        path = self.person_dir(person_key) / "roleplay.json"
        if not path.is_file():
            return {"enabled": False, "mode": "off", "participant_consented": False, "persona_label": ""}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"enabled": False, "mode": "off", "participant_consented": False, "persona_label": ""}

    def set_roleplay_state(self, person_key: str, payload: dict[str, Any]) -> None:
        allowed = {
            "enabled": bool(payload.get("enabled", False)),
            "mode": str(payload.get("mode", "off"))[:40],
            "participant_consented": bool(payload.get("participant_consented", False)),
            "persona_label": str(payload.get("persona_label", ""))[:80],
        }
        _atomic_json(self.ensure(person_key) / "roleplay.json", allowed)

    def export(self, person_key: str) -> dict[str, Any]:
        target = self.person_dir(person_key)
        if not target.is_dir():
            return {"person_key": person_key, "files": {}}
        files: dict[str, Any] = {}
        for path in sorted(target.rglob("*")):
            rel_parts = path.relative_to(target).parts
            if not path.is_file() or "derived" in rel_parts or "security" in rel_parts:
                continue
            rel = path.relative_to(target).as_posix()
            if path.suffix == ".json":
                files[rel] = json.loads(path.read_text(encoding="utf-8"))
            elif path.suffix == ".jsonl":
                files[rel] = [
                    json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
                ]
        return {"person_key": person_key, "files": files}

    def clear_derived(self, person_key: str) -> None:
        derived = self.person_dir(person_key) / "derived"
        if derived.exists():
            shutil.rmtree(derived)
        derived.mkdir(parents=True, exist_ok=True)

    def delete(self, person_key: str) -> bool:
        target = self.person_dir(person_key)
        if not target.exists():
            return False
        shutil.rmtree(target)
        return True

    def iter_candidate_records(self, person_key: str) -> Iterable[dict[str, Any]]:
        path = self.person_dir(person_key) / "facts.jsonl"
        if not path.is_file():
            return ()
        return tuple(
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
        )
