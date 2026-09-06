from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
import json, time
from pathlib import Path
from .models import Observation

def canonical_digest(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()

@dataclass(frozen=True)
class EnrolledSource:
    source_ref: str
    source_revision: str
    owner_id: str
    scope_id: str
    objective_digest: str

class FileStateObserver:
    """Deterministic local fixture observer; never calls a model."""
    def __init__(self, enrolled: EnrolledSource, path):
        self.enrolled = enrolled
        self.path = Path(path)

    def observe(self, *, now=None) -> Observation:
        now = time.time() if now is None else now
        if self.path.exists() and self.path.is_file():
            data = self.path.read_bytes()
            values = {"exists": True, "sha256": sha256(data).hexdigest(), "size_bytes": len(data)}
        else:
            values = {"exists": False, "sha256": None, "size_bytes": 0}
        identity = {
            "source_ref": self.enrolled.source_ref,
            "source_revision": self.enrolled.source_revision,
            "objective_digest": self.enrolled.objective_digest,
            "observed_at": now,
            "values": values,
        }
        return Observation(
            observation_id=canonical_digest(identity),
            owner_id=self.enrolled.owner_id,
            scope_id=self.enrolled.scope_id,
            objective_digest=self.enrolled.objective_digest,
            source_ref=self.enrolled.source_ref,
            source_revision=self.enrolled.source_revision,
            observed_at=now,
            values=values,
            provenance={"kind":"local_file","path":str(self.path)},
        )
