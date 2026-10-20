
"""Atomic file transport for future App -> Bossman Local Task Exchange.
This is intentionally NOT a second task engine.
"""
from __future__ import annotations
from pathlib import Path
import json, os, tempfile, uuid
from datetime import datetime, timezone

APP_ID = "pc-autopilot-mini"


def exchange_base() -> Path:
    """The only folder task files may be written under: $BOSSMAN_APP_EXCHANGE_ROOT, else this app's own
    data folder (apps/<app_id>/data — Bossman's worker reads apps/<app_id>/data/bossman/inbox)."""
    env = os.getenv("BOSSMAN_APP_EXCHANGE_ROOT")
    return Path(env) if env else Path(__file__).resolve().parents[2] / "data"


def resolve_exchange_root(requested: str) -> Path:
    """A caller-supplied exchange root, confined to exchange_base(); relative roots are relative to it.
    Raises ValueError for anything that resolves outside (absolute elsewhere, «..», symlink escape)."""
    base = exchange_base().resolve()
    root = Path(requested)
    root = (root if root.is_absolute() else base / root).resolve()
    if root != base and base not in root.parents:
        raise ValueError("exchange_root must be inside the app's task exchange folder")
    return root

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
