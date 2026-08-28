from __future__ import annotations

from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True, slots=True)
class ForkRequest:
    original_run_id: int
    checkpoint_step: int
    new_agent_id: int | None = None
    new_model_id: int | None = None
    instruction_override: str | None = None

def fork_checkpoint(checkpoint: dict[str, Any], req: ForkRequest) -> dict[str, Any]:
    """Returns a deep-copy-friendly checkpoint payload for a new run."""
    messages = [dict(m) for m in (checkpoint.get("messages") or [])]
    if req.instruction_override:
        messages.append({"role": "user", "content": req.instruction_override})
    return {
        "messages": messages,
        "step": req.checkpoint_step,
        "note": f"forked from run {req.original_run_id} step {req.checkpoint_step}",
        "lineage": {
            "parent_run_id": req.original_run_id,
            "parent_step": req.checkpoint_step,
            "agent_override": req.new_agent_id,
            "model_override": req.new_model_id,
        },
    }
