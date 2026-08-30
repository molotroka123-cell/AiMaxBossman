
from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field

JOB_TYPES = ['import_transactions', 'categorize', 'build_pnl', 'build_cashflow', 'anomaly_scan', 'owner_report']

class JobCreate(BaseModel):
    type: str
    params: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
