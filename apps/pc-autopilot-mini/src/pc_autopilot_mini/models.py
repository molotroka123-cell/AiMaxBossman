
from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field

JOB_TYPES = ['record', 'compile_macro', 'validate_macro', 'run_macro', 'repair_macro', 'audit_run']

class JobCreate(BaseModel):
    type: str
    params: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
