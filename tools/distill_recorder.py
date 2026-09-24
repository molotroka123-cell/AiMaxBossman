#!/usr/bin/env python3
"""Thin recorder: every worker-model call -> one JSONL record for later distillation.

Owner request 2026-09-24: a strong free cloud model works as the WORKER, Claude
curates, and every call is kept so local models can later learn from it.

This is the RAW end of the existing Dataset Gate
(bossman-core/bossman/sandbox/dataset.py): records land as `RAW_CANDIDATE`,
never directly as training data. The human/quality gate stays manual.

Each record: ts, model, source_sha, task_class, messages, tools schema,
response text, tool calls, latency, tokens, verifier verdict, curator note and
the licence finding. Secrets are scrubbed with bossman.obs.redact_obj (the
same redactor the product logs use); if that import is unavailable the
recorder refuses to write rather than write unscrubbed.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import threading
import time
import uuid
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_DIR = pathlib.Path(os.environ.get("BOSSMAN_DISTILL_DIR", r"C:\Users\asd\Bossman\datasets\distill-20260924"))

# Licence findings per worker model (checked 2026-09-24, sources in the note).
LICENSES: dict[str, dict[str, str]] = {
    "nvidia/nemotron-3-ultra-550b-a55b:free": {
        "model_license": "OpenMDW-1.1 (HF nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-BF16 card)",
        "outputs": "OpenMDW-1.1: outputs are not restricted by the model license",
        "privacy": "OpenRouter :free endpoint may retain prompts; public/non-private inputs only",
        "training_use": "ALLOWED_BY_MODEL_LICENSE",
    },
    "inclusionai/ling-3.0-flash-fin:free": {
        "model_license": "UNVERIFIED_IN_REPO",
        "outputs": "UNVERIFIED_IN_REPO",
        "privacy": "OpenRouter :free endpoint; public/non-private inputs only",
        "training_use": "QUARANTINE_UNTIL_LICENSE_VERIFIED",
    },
    "z-ai/glm-5.3-flash": {
        "model_license": "UNVERIFIED_IN_REPO",
        "outputs": "UNVERIFIED_IN_REPO",
        "privacy": "Paid OpenRouter endpoint; public/non-private inputs only for this owner run",
        "training_use": "QUARANTINE_UNTIL_LICENSE_VERIFIED",
    },
}
UNKNOWN_LICENSE = {"model_license": "UNVERIFIED", "outputs": "UNVERIFIED", "privacy": "UNVERIFIED",
                   "training_use": "UNVERIFIED"}


def _redactor():
    try:
        sys.path.insert(0, str(ROOT / "bossman-core"))
        from bossman.obs import redact_obj  # noqa: PLC0415
        return redact_obj
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"secret redactor unavailable, refusing to record: {exc}") from exc


def source_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              timeout=10).stdout.strip() or "unknown"
    except OSError:
        return "unknown"


class Recorder:
    def __init__(self, name: str, *, directory: pathlib.Path | None = None):
        self.dir = pathlib.Path(directory or DEFAULT_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{name}.jsonl"
        self.sha = source_sha()
        self._redact = _redactor()
        self._lock = threading.Lock()
        self.count = 0

    def record(self, *, model: str, task_class: str, messages: list[dict], response_text: str,
               tool_calls: list | None = None, tools: list | None = None, reasoning: str | None = None,
               latency_s: float | None = None, ttft_ms: float | None = None,
               usage: dict | None = None, verdict: str = "UNVERIFIED", verifier: str = "",
               curator_note: str = "", error: str | None = None, extra: dict | None = None) -> str:
        rid = uuid.uuid4().hex
        lic = LICENSES.get(model, UNKNOWN_LICENSE)
        rec: dict[str, Any] = {
            "schema": "bossman.distill.raw.v1", "id": rid,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "model": model, "source_sha": self.sha, "task_class": task_class,
            "messages": messages, "tools": tools or [], "response": response_text,
            "reasoning": reasoning, "tool_calls": tool_calls or [],
            "latency_s": latency_s, "ttft_ms": ttft_ms, "usage": usage or {},
            "verifier_verdict": verdict, "verifier": verifier, "curator_note": curator_note,
            "error": error, "license": lic, "training_use": lic["training_use"],
            "gate_state": "RAW_CANDIDATE", "extra": extra or {},
        }
        line = json.dumps(self._redact(rec), ensure_ascii=False)
        with self._lock:
            with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(line + "\n")
            self.count += 1
        return rid
