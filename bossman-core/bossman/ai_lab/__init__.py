"""Stage 11 — AI Lab: приватный fine-tuning конвейер поверх Stage 8.

raw trajectory (read-only) → sanitize → validate → candidate → human gate
→ bounded eval → SFT/DPO export. Training launch выключен по умолчанию.
Вторых memory/dataset/gateway/scheduler/sandbox не создаёт.
"""
from __future__ import annotations

from .candidates import AiLabCandidate, CandidateStore, load_trajectory
from .export import (EvalRunner, Exporter, LocalTrainingAdapter,
                     MAX_EVAL_CASES, TrainingDisabled)
from .sanitizer import SANITIZER_VERSION, sanitize_obj, sanitize_text
from .routes import router
from .routes import router as lab_router

__all__ = [
    "AiLabCandidate", "CandidateStore", "load_trajectory",
    "EvalRunner", "Exporter", "LocalTrainingAdapter", "MAX_EVAL_CASES",
    "TrainingDisabled", "SANITIZER_VERSION", "sanitize_obj", "sanitize_text",
    "lab_router",
    "router",
]
