"""Durable owner-input requests for unattended Bossman work.

Bossman may discover a form field or external-account fact it cannot know.
It asks the owner, stores the answer encrypted, and the browser runtime fills
the field without exposing the value to the model. This is INPUT, not approval:
submit/login/payment/ToS remain governed by their existing ASK/DENY policies.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

MAX_FIELDS = 16
MAX_VALUE = 4000
TTL_SECONDS = 24 * 3600


class OwnerInputError(RuntimeError):
    pass


class OwnerInputStore:
    def __init__(self, path: Path, vault):
        self.path = Path(path)
        self.vault = vault
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {"requests": {}}
        except (OSError, ValueError):
            return {"requests": {}}

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    @staticmethod
    def _public(row: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in row.items() if k != "answers_enc"}

    @staticmethod
    def _validate_fields(fields: Any) -> list[dict[str, Any]]:
        if not isinstance(fields, list) or not 1 <= len(fields) <= MAX_FIELDS:
            raise OwnerInputError("fields must be a non-empty bounded list")
        out, seen = [], set()
        for raw in fields:
            if not isinstance(raw, dict):
                raise OwnerInputError("field must be an object")
            key = str(raw.get("key") or "").strip()
            label = str(raw.get("label") or key).strip()
            ref = str(raw.get("ref") or "").strip()
            selector = str(raw.get("selector") or "").strip()
            if not key or len(key) > 64 or key in seen:
                raise OwnerInputError("field keys must be unique and <=64 chars")
            if not label or len(label) > 160 or not (ref or selector):
                raise OwnerInputError("field requires label and ref/selector")
            seen.add(key)
            out.append({"key": key, "label": label, "ref": ref[:120],
                        "selector": selector[:300], "secret": bool(raw.get("secret", False)),
                        "hint": str(raw.get("hint") or "")[:240]})
        return out

    def create(self, *, task_id: int | None, session_id: int, fields: Any,
               context: str = "", source: str = "browser") -> dict[str, Any]:
        normalized = self._validate_fields(fields)
        now = time.time()
        row = {
            "id": uuid.uuid4().hex[:12], "status": "PENDING", "created_at": now,
            "expires_at": now + TTL_SECONDS, "task_id": task_id,
            "session_id": int(session_id), "source": source[:64],
            "context": str(context or "")[:500], "fields": normalized,
            "answered_by": None, "answered_at": None, "answers_enc": "",
        }
        with self._lock:
            data = self._read()
            data.setdefault("requests", {})[row["id"]] = row
            self._write(data)
        return self._public(row)

    def get(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = (self._read().get("requests") or {}).get(str(request_id))
            return self._public(dict(row)) if isinstance(row, dict) else None

    def pending(self) -> list[dict[str, Any]]:
        now = time.time()
        out = []
        with self._lock:
            data = self._read()
            changed = False
            for row in (data.get("requests") or {}).values():
                if not isinstance(row, dict):
                    continue
                if row.get("status") in {"PENDING", "ANSWERED"} and float(row.get("expires_at") or 0) < now:
                    row["status"] = "EXPIRED"; row["answers_enc"] = ""; changed = True
                if row.get("status") == "PENDING":
                    out.append(self._public(dict(row)))
            if changed:
                self._write(data)
        return sorted(out, key=lambda r: r.get("created_at", 0))

    def answer(self, request_id: str, values: Any, *, actor: str) -> dict[str, Any]:
        if not isinstance(values, dict):
            raise OwnerInputError("values must be an object")
        with self._lock:
            data = self._read()
            row = (data.get("requests") or {}).get(str(request_id))
            if not isinstance(row, dict) or row.get("status") != "PENDING":
                raise OwnerInputError("request is absent, expired or already answered")
            if float(row.get("expires_at") or 0) < time.time():
                row["status"] = "EXPIRED"; self._write(data)
                raise OwnerInputError("request expired")
            expected = {f["key"] for f in row.get("fields") or []}
            if set(values) != expected:
                raise OwnerInputError("answer keys must exactly match requested fields")
            clean = {}
            for key, value in values.items():
                if not isinstance(value, (str, int, float, bool)) or len(str(value)) > MAX_VALUE:
                    raise OwnerInputError(f"invalid value for {key}")
                clean[key] = str(value)
            row["answers_enc"] = self.vault.encrypt(json.dumps(clean, ensure_ascii=False))
            row["status"] = "ANSWERED"; row["answered_by"] = str(actor)[:120]
            row["answered_at"] = time.time()
            self._write(data)
            return self._public(dict(row))

    def values_for_fill(self, request_id: str, *, task_id: int | None) -> tuple[dict, dict[str, str]]:
        with self._lock:
            data = self._read()
            row = (data.get("requests") or {}).get(str(request_id))
            if not isinstance(row, dict) or row.get("status") != "ANSWERED":
                raise OwnerInputError("request is not answered")
            if row.get("task_id") not in (None, task_id):
                raise OwnerInputError("request belongs to another task")
            raw = self.vault.decrypt(str(row.get("answers_enc") or ""))
            if raw is None:
                raise OwnerInputError("cannot decrypt owner input")
            values = json.loads(raw)
            if not isinstance(values, dict):
                raise OwnerInputError("owner input payload invalid")
            return dict(row), {str(k): str(v) for k, v in values.items()}

    def mark_filled(self, request_id: str) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            row = (data.get("requests") or {}).get(str(request_id))
            if not isinstance(row, dict) or row.get("status") != "ANSWERED":
                raise OwnerInputError("request is not ready to mark filled")
            row["status"] = "FILLED"; row["filled_at"] = time.time()
            row["answers_enc"] = ""
            self._write(data)
            return self._public(dict(row))

    def cancel(self, request_id: str) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            row = (data.get("requests") or {}).get(str(request_id))
            if not isinstance(row, dict):
                raise OwnerInputError("request not found")
            if row.get("status") not in {"PENDING", "ANSWERED"}:
                return self._public(dict(row))
            row["status"] = "CANCELLED"; row["answers_enc"] = ""
            self._write(data)
            return self._public(dict(row))
