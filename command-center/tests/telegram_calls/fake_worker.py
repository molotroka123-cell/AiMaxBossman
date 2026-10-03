"""A tiny stand-in for ``python -I -m bcc.telegram_calls`` speaking the same stdio JSON-lines protocol.

Used by ``test_manager.py`` for the failure modes the real worker cannot be forced into on demand (silence on STOP, dying in
the middle of a call, dying before it answers a dial). Its behaviour is chosen by ``FAKE_WORKER_MODE`` and it records what it
saw into ``FAKE_WORKER_LOG`` (one JSON line per request: the op and the KEYS of its args, never the values).

It is a test double of the transport contract only; nothing here proves anything about real Telegram or the real worker.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

MODE = os.environ.get("FAKE_WORKER_MODE", "normal")
LOG = Path(os.environ["FAKE_WORKER_LOG"]) if os.environ.get("FAKE_WORKER_LOG") else None
HOME = Path(os.environ.get("BCC_DATA_DIR", ".")) / "telegram-calls"
STARTED = Path(os.environ["FAKE_WORKER_STARTED"]) if os.environ.get("FAKE_WORKER_STARTED") else None


def out(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def note(entry: dict) -> None:
    if LOG is not None:
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")


def write_state(data: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    (HOME / "state.json").write_text(json.dumps(data), encoding="utf-8")


def main() -> int:
    if STARTED is not None:
        STARTED.write_text("started", encoding="utf-8")
    out({"event": "state", "state": "worker_ready", "pid": os.getpid()})
    call_id = "c-fake000001"
    for raw in sys.stdin:
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        op, rid, args = msg.get("op"), msg.get("id"), msg.get("args") or {}
        note({"op": op, "args_keys": sorted(args), "stop_file_present": (HOME / "STOP").exists()})
        if op == "hello":
            out({"id": rid, "ok": True, "result": {"version": "fake", "pid": os.getpid(),
                                                   "mode": "offline_test" if MODE != "telegram" else "telegram",
                                                   "transport": "loopback"}})
        elif op == "status":
            out({"id": rid, "ok": True, "result": {"account": {"state": "ready"}, "call": None, "stop": (HOME / "STOP").exists()}})
        elif op in ("login.start", "login.code"):
            out({"id": rid, "ok": True, "result": {"state": "code_sent" if op == "login.start" else "ready"}})
        elif op == "contacts":
            out({"id": rid, "ok": True, "result": {"contacts": [{"id": 5, "label": "Второй", "username": "second"}]}})
        elif op == "dial":
            write_state({"in_flight": call_id, "started": time.time(), "last_outcome": None})
            if MODE == "die_before_reply":
                os._exit(3)
            out({"id": rid, "ok": True, "result": {"call_id": call_id, "accepted": True, "transport": "loopback",
                                                   "models": {"llm": "fake-llm"}, "notes": {}}})
            out({"event": "call_event", "data": {"seq": 1, "kind": "state", "at": time.time(), "state": "active"}})
            if MODE == "die_after_dial":
                time.sleep(0.2)
                os._exit(4)
        elif op == "hangup":
            out({"id": rid, "ok": True, "result": {"ended": True}})
        elif op == "stop":
            if MODE == "hang_on_stop":
                continue                                        # never acknowledges
            (HOME / "STOP").write_text("{}", encoding="utf-8") if not (HOME / "STOP").exists() else None
            confirmed = MODE != "unconfirmed_stop"
            out({"id": rid, "ok": True, "result": {"stopped": True, "hangup_confirmed": confirmed, "stop_flag": True}})
        elif op == "resume":
            out({"id": rid, "ok": True, "result": {"stop_flag": False}})
        elif op == "shutdown":
            out({"id": rid, "ok": True, "result": {"bye": True}})
            return 0
        elif op == "slow":
            time.sleep(30)
        else:
            out({"id": rid, "ok": False, "error": {"code": "INTERNAL", "message": "unknown", "hint": ""}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
