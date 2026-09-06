from __future__ import annotations
import hashlib, json, os, re, tempfile, time, uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from bossman_v3 import evidence as _signing

CHECKPOINT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class CheckpointIntegrityError(ValueError):
    """A checkpoint that cannot be authenticated is not a checkpoint."""


@dataclass(frozen=True)
class Checkpoint:
    checkpoint_id: str
    created_at: str
    state: Mapping[str, Any]
    verified: bool
    state_hash: str
    # Signature over the WHOLE record. `verified` and `state_hash` live in the same
    # file as the state, so a plain self-hash proves nothing: whoever can rewrite
    # the state can rewrite both. Only a process holding the evidence key may
    # produce a record that restore() will accept (EH-01, fail-closed).
    sig: str = ""
    signer: str = ""
    nonce: str = ""
    issued_at: str = ""

    def body(self) -> dict[str, Any]:
        return {"checkpoint_id": self.checkpoint_id, "created_at": self.created_at,
                "state": dict(self.state), "verified": bool(self.verified),
                "state_hash": self.state_hash, "record_type": "recovery_checkpoint",
                "signer": self.signer, "nonce": self.nonce, "issued_at": self.issued_at}

    def signature_valid(self) -> bool:
        return bool(self.sig) and _signing.verify_signed({**self.body(), "sig": self.sig})


class FileCheckpointStore:
    """Demo/test store only. Production should adapt canonical Bossman persistence.

    Writes are atomic (temp file + os.replace + fsync): an interrupted save leaves
    the previous checkpoint intact rather than a truncated one that parses.
    """
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve(); self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, checkpoint_id: str) -> Path:
        if not isinstance(checkpoint_id, str) or not CHECKPOINT_ID.fullmatch(checkpoint_id):
            raise CheckpointIntegrityError("invalid checkpoint identifier")
        path = self.root / f"{checkpoint_id}.json"
        if path.is_symlink() or path.parent != self.root:
            raise CheckpointIntegrityError("checkpoint path escapes storage root")
        return path

    def save(self, checkpoint: Checkpoint) -> str:
        p = self._path(checkpoint.checkpoint_id)
        fd, temp = tempfile.mkstemp(prefix=".checkpoint-", dir=str(self.root))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(asdict(checkpoint), stream, sort_keys=True, default=str)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(temp, p)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        return checkpoint.checkpoint_id

    def load(self, checkpoint_id: str) -> Checkpoint:
        raw = json.loads(self._path(checkpoint_id).read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or set(raw) - {f.name for f in fields_of(Checkpoint)}:
            raise CheckpointIntegrityError("unknown checkpoint schema")
        return Checkpoint(**raw)


def fields_of(cls):
    import dataclasses
    return dataclasses.fields(cls)

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
        cp=Checkpoint(uuid.uuid4().hex, datetime.now(timezone.utc).isoformat(), dict(state), bool(verified),
                      self._state_hash(state))
        cp=Checkpoint(**{**asdict(cp), **_signing.sign_fields(cp.body(), signer=_signing.VERIFIER_SIGNER)})
        self.store.save(cp); return cp

    def restore(self, checkpoint_id: str) -> Mapping[str,Any]:
        cp=self.store.load(checkpoint_id)
        # Authenticate BEFORE reading any flag out of the file: an unsigned or
        # tampered record must never decide "this state was VERIFIED".
        if not cp.signature_valid():
            raise CheckpointIntegrityError("unsigned or tampered checkpoint; reconciliation required")
        if not cp.verified: raise PermissionError("only verified checkpoints may be restored")
        if cp.state_hash != self._state_hash(cp.state): raise ValueError("checkpoint hash mismatch")
        return cp.state

    def latest_verified(self, checkpoint_ids) -> Checkpoint | None:
        """Newest checkpoint that is both signed and VERIFIED; unusable ones are skipped."""
        usable=[]
        for cid in checkpoint_ids:
            try:
                cp=self.store.load(cid)
            except (OSError, ValueError):
                continue
            if cp.signature_valid() and cp.verified and cp.state_hash==self._state_hash(cp.state):
                usable.append(cp)
        return max(usable, key=lambda c: c.created_at, default=None)

    def evaluate(self, *, spent: float, action: Mapping[str,Any], state: Mapping[str,Any], outcome: Mapping[str,Any], last_verified_checkpoint: str|None=None) -> RecoveryDirective | None:
        if spent > self.budget_limit:
            return RecoveryDirective("ABORT_OR_APPROVAL", "budget runaway detected", last_verified_checkpoint)
        if self.loop.observe(action,state,outcome):
            return RecoveryDirective("REPLAN", "repeated action/state/outcome loop detected", last_verified_checkpoint)
        if self.watchdog.stale():
            return RecoveryDirective("RECOVER", "watchdog heartbeat stale", last_verified_checkpoint)
        return None
