from __future__ import annotations
import hashlib, json, time, uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

@dataclass(frozen=True)
class Checkpoint:
    checkpoint_id: str
    created_at: str
    state: Mapping[str, Any]
    verified: bool
    state_hash: str

class FileCheckpointStore:
    """Demo/test store only. Production should adapt canonical Bossman persistence."""
    def __init__(self, root: str | Path):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
    def save(self, checkpoint: Checkpoint) -> str:
        p=self.root/f"{checkpoint.checkpoint_id}.json"
        p.write_text(json.dumps(asdict(checkpoint), sort_keys=True, default=str), encoding="utf-8")
        return checkpoint.checkpoint_id
    def load(self, checkpoint_id: str) -> Checkpoint:
        d=json.loads((self.root/f"{checkpoint_id}.json").read_text(encoding="utf-8"))
        return Checkpoint(**d)

class LoopDetector:
    def __init__(self, repeat_limit: int = 3): self.repeat_limit=repeat_limit; self._last=None; self._count=0
    def observe(self, action: Mapping[str,Any], state: Mapping[str,Any], outcome: Mapping[str,Any]) -> bool:
        raw=json.dumps([action,state,outcome], sort_keys=True, default=str).encode()
        h=hashlib.sha256(raw).hexdigest()
        if h==self._last: self._count+=1
        else: self._last=h; self._count=1
        return self._count >= self.repeat_limit

class Watchdog:
    def __init__(self, stale_after_seconds: float=60): self.stale_after_seconds=stale_after_seconds; self.last_heartbeat=time.monotonic()
    def heartbeat(self): self.last_heartbeat=time.monotonic()
    def stale(self) -> bool: return time.monotonic()-self.last_heartbeat > self.stale_after_seconds

@dataclass(frozen=True)
class RecoveryDirective:
    kind: str
    reason: str
    checkpoint_id: str | None = None

class RecoveryKernel:
    def __init__(self, store: FileCheckpointStore, *, budget_limit: float=100.0, repeat_limit: int=3):
        self.store=store; self.budget_limit=budget_limit; self.loop=LoopDetector(repeat_limit); self.watchdog=Watchdog()

    @staticmethod
    def _state_hash(state: Mapping[str,Any]) -> str:
        return hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()

    def checkpoint(self, state: Mapping[str,Any], *, verified: bool) -> Checkpoint:
        cp=Checkpoint(str(uuid.uuid4()), datetime.now(timezone.utc).isoformat(), dict(state), verified, self._state_hash(state))
        self.store.save(cp); return cp

    def restore(self, checkpoint_id: str) -> Mapping[str,Any]:
        cp=self.store.load(checkpoint_id)
        if not cp.verified: raise PermissionError("only verified checkpoints may be restored")
        if cp.state_hash != self._state_hash(cp.state): raise ValueError("checkpoint hash mismatch")
        return cp.state

    def evaluate(self, *, spent: float, action: Mapping[str,Any], state: Mapping[str,Any], outcome: Mapping[str,Any], last_verified_checkpoint: str|None=None) -> RecoveryDirective | None:
        if spent > self.budget_limit:
            return RecoveryDirective("ABORT_OR_APPROVAL", "budget runaway detected", last_verified_checkpoint)
        if self.loop.observe(action,state,outcome):
            return RecoveryDirective("REPLAN", "repeated action/state/outcome loop detected", last_verified_checkpoint)
        if self.watchdog.stale():
            return RecoveryDirective("RECOVER", "watchdog heartbeat stale", last_verified_checkpoint)
        return None
