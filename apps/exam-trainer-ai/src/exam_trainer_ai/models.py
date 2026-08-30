
from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field

JOB_TYPES = ['collect_sources', 'build_blueprint', 'diagnostic', 'generate_training', 'mock_exam', 'analyze_errors']

class JobCreate(BaseModel):
    type: str
    params: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
