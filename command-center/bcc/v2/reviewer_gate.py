from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

GateStatus = Literal["working", "review", "fix", "passed", "failed", "waiting_approval"]

@dataclass(slots=True)
class ReviewGate:
    max_iterations: int = 3
    iteration: int = 0
    status: GateStatus = "working"
    last_feedback: str = ""

    def submit_for_review(self) -> None:
        if self.status not in ("working", "fix"):
            raise RuntimeError(f"cannot submit from {self.status}")
        self.status = "review"

    def review_result(self, passed: bool, feedback: str = "") -> GateStatus:
        if self.status != "review":
            raise RuntimeError("review result without review state")
        self.last_feedback = feedback
        if passed:
            self.status = "passed"
            return self.status
        self.iteration += 1
        if self.iteration >= self.max_iterations:
            self.status = "waiting_approval"
        else:
            self.status = "fix"
        return self.status
