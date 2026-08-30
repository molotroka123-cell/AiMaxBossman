
from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field

JOB_TYPES = ['scan', 'plan', 'rename', 'deduplicate', 'organize', 'archive']

class JobCreate(BaseModel):
    type: str
    params: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
