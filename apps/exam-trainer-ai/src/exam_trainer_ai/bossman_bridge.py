
"""Atomic file transport for future App -> Bossman Local Task Exchange.
This is intentionally NOT a second task engine.
"""
from __future__ import annotations
from pathlib import Path
import json, os, tempfile, uuid
from datetime import datetime, timezone

APP_ID = "exam-trainer-ai"

def emit_task(root: Path, task_type: str, payload: dict, capabilities: list[str]) -> Path:
    inbox = root / "bossman" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    task_id = str(uuid.uuid4())
    body = {
        "task_id": task_id, "app_id": APP_ID, "type": task_type, "priority": "normal",
        "created_at": datetime.now(timezone.utc).isoformat(), "input": payload,
        "requested_capabilities": capabilities, "idempotency_key": task_id,
        "reply_to": f"bossman/completed/{task_id}.json"
    }
    fd, tmp = tempfile.mkstemp(prefix=".task-",suffix=".json",dir=inbox)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(body,f,ensure_ascii=False,indent=2); f.flush(); os.fsync(f.fileno())
        final = inbox / f"{task_id}.json"
        os.replace(tmp,final)
        return final
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
