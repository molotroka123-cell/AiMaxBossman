"""FableTranscriptRecorder: append-only agent that captures every paid-model
exchange (bundle -> response -> usage -> provider request_id) as machine-parseable
JSONL, so a local model can later be trained on or documentation generated from
the corpus.

Storage layout:
    <root>/<mission_id>/transcript.jsonl      one JSON object per exchange
    <root>/<mission_id>/training_pairs.jsonl  export: chat-format supervised pairs
    <root>/index.jsonl                        cross-mission index (one line per exchange)

Safety: bundle/response text is passed through the shared trace redactor when
available, so API keys and token-like secrets never enter the corpus.  Hidden
chain-of-thought is never requested from providers, so nothing hidden is stored.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any


def _redact(text: str) -> str:
    try:
        from ._bootstrap import trace

        return trace().redact_text(text)
    except Exception:
        return text


class FableTranscriptRecorder:
    """Durable, append-only recorder.  Appends never rewrite existing bytes; a
    corrupted trailing line does not prevent reading earlier ones."""

    SCHEMA_VERSION = 1

    def __init__(self, root: Path | str, mission_id: str, owner_id: str = "bossman") -> None:
        self.root = Path(root)
        self.mission_id = mission_id
        self.owner_id = owner_id
        self._lock = threading.Lock()
        self._dir = self.root / mission_id
        self._dir.mkdir(parents=True, exist_ok=True)

    # -- core recording ----------------------------------------------------
    def record(self, *, bundle: Any, response_text: str, usage: dict[str, Any] | None = None,
               request_id: str = "", purpose: str = "", stop_reason: str = "") -> dict[str, Any]:
        entry = {
            "schema_version": self.SCHEMA_VERSION,
            "exchange_id": uuid.uuid4().hex,
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "mission_id": self.mission_id,
            "owner_id": self.owner_id,
            "purpose": purpose,
            "request_id": request_id,
            "model": (usage or {}).get("model", ""),
            "stop_reason": stop_reason,
            "usage": {k: (usage or {}).get(k) for k in (
                "input_tokens", "output_tokens", "cache_read_input_tokens",
                "cache_creation_input_tokens", "estimated_cost_usd", "latency_ms")},
            "bundle": _redact(json.dumps(bundle, sort_keys=True, default=str)),
            "response_text": _redact(response_text),
        }
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with self._lock:
            path = self._dir / "transcript.jsonl"
            if path.exists():
                # a previous partial write may lack its trailing newline: repair the
                # boundary so a corrupted fragment cannot swallow the next record
                with open(path, "rb") as fh:
                    fh.seek(-1, os.SEEK_END)
                    if fh.read(1) != b"\n":
                        with open(path, "a", encoding="utf-8") as afh:
                            afh.write("\n")
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            with open(self.root / "index.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps({k: entry[k] for k in (
                    "exchange_id", "recorded_at", "mission_id", "purpose",
                    "request_id", "model", "usage")}, default=str) + "\n")
        return entry

    # -- reading / export ---------------------------------------------------
    def read(self) -> list[dict[str, Any]]:
        path = self._dir / "transcript.jsonl"
        if not path.exists():
            return []
        out: list[dict[str, Any]] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                out.append(json.loads(raw))
            except json.JSONDecodeError:
                continue  # corrupted trailing line: keep earlier records
        return out

    def export_training_pairs(self, *, min_response_chars: int = 1) -> list[dict[str, Any]]:
        """Export chat-format supervised pairs ready for local-model fine-tuning.

        Only completed exchanges (stop_reason == 'end_turn') with a non-trivial
        response become pairs; truncated calls are exported with
        ``meta.truncated = true`` so the trainer can filter them.
        """
        pairs: list[dict[str, Any]] = []
        for entry in self.read():
            response = entry.get("response_text", "")
            if len(response) < min_response_chars:
                continue
            pairs.append({
                "messages": [
                    {"role": "user", "content": entry.get("bundle", "")},
                    {"role": "assistant", "content": response},
                ],
                "meta": {
                    "mission_id": entry["mission_id"], "purpose": entry.get("purpose", ""),
                    "model": entry.get("model", ""), "request_id": entry.get("request_id", ""),
                    "truncated": entry.get("stop_reason") == "max_tokens",
                    "usage": entry.get("usage", {}),
                },
            })
        if pairs:
            with open(self._dir / "training_pairs.jsonl", "w", encoding="utf-8") as fh:
                for pair in pairs:
                    fh.write(json.dumps(pair, ensure_ascii=False, default=str) + "\n")
        return pairs


def recorder_from_env(mission_id: str) -> FableTranscriptRecorder | None:
    """Build a recorder from BOSSMAN_FABLE_TRANSCRIPT_DIR (default: OFF)."""
    root = os.environ.get("BOSSMAN_FABLE_TRANSCRIPT_DIR", "")
    if not root:
        return None
    return FableTranscriptRecorder(root, mission_id)
