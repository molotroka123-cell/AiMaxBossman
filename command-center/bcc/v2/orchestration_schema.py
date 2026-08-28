from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

@dataclass(slots=True)
class OrchestraDraft:
    name: str
    manager_agent: str | None = None
    worker_agents: list[str] = field(default_factory=list)
    reviewer_agent: str | None = None
    max_workers: int = 1
    max_runtime_minutes: int = 60
    cloud_budget_usd: float = 0.0
    permissions: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def validate(self, known_agents: set[str]) -> list[str]:
        errors: list[str] = []
        refs = [x for x in [self.manager_agent, self.reviewer_agent] if x] + self.worker_agents
        for name in refs:
            if name not in known_agents:
                errors.append(f"unknown agent: {name}")
        if not 1 <= self.max_workers <= 32:
            errors.append("max_workers must be 1..32")
        if not 1 <= self.max_runtime_minutes <= 10080:
            errors.append("max_runtime_minutes must be 1..10080")
        if self.cloud_budget_usd < 0:
            errors.append("cloud budget cannot be negative")
        return errors
