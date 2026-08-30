
from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field

JOB_TYPES = ['search_trip', 'compare_packages', 'optimize_dates', 'build_itinerary', 'watch_price', 'trip_report']

class JobCreate(BaseModel):
    type: str
    params: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
