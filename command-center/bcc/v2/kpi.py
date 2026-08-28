from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Agg = Literal["sum", "set", "max"]

@dataclass(slots=True)
class KPI:
    key: str
    label: str
    target: float | None = None
    current: float = 0.0
    unit: str = ""
    aggregation: Agg = "sum"

    @property
    def progress(self) -> float | None:
        if self.target is None or self.target <= 0:
            return None
        return max(0.0, min(self.current / self.target, 1.0))

    def apply(self, value: float) -> None:
        if self.aggregation == "sum":
            self.current += value
        elif self.aggregation == "set":
            self.current = value
        elif self.aggregation == "max":
            self.current = max(self.current, value)
        else:
            raise ValueError(f"unknown aggregation: {self.aggregation}")

def mission_progress(kpis: list[KPI]) -> float:
    weighted = [k.progress for k in kpis if k.progress is not None]
    return sum(weighted) / len(weighted) if weighted else 0.0
