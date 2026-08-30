
from __future__ import annotations
from pathlib import Path
import json, os, time
from typing import Any

class LocalTaskExchangeClient:
    """App-side thin client. It does not claim Bossman tasks or execute agents."""
    def __init__(self, root: Path):
        self.root = Path(root)
        for d in ("inbox","claimed","completed","failed","artifacts"):
            (self.root/"bossman"/d).mkdir(parents=True, exist_ok=True)

    def result(self, task_id: str) -> dict[str, Any] | None:
        for bucket in ("completed","failed"):
            p=self.root/"bossman"/bucket/f"{task_id}.json"
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        return None

    def wait(self, task_id: str, timeout: float = 0.0, poll: float = 0.2):
        end=time.time()+max(timeout,0)
        while True:
            r=self.result(task_id)
            if r is not None: return r
            if timeout<=0 or time.time()>=end: return None
            time.sleep(poll)
