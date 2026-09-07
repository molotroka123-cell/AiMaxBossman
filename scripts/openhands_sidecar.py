"""OpenHands sidecar entrypoint.

Install this script in a Python 3.12+ environment with the OpenHands SDK.
It reads one Bossman request from stdin and emits one JSON response to stdout.
Provider credentials are inherited by the sidecar runtime; they are never
placed in argv or echoed in the response.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

SCHEMA = "bossman.openhands.v1"


def _emit(status: str, **extra: object) -> None:
    print(json.dumps({"schema": SCHEMA, "status": status, **extra}, separators=(",", ":")))


def main() -> int:
    try:
        req = json.load(sys.stdin)
        if req.get("schema") != SCHEMA:
            raise ValueError("unsupported schema")
        workspace = Path(req["workspace"]).resolve()
        if not workspace.is_dir():
            raise ValueError("workspace does not exist")

        # Imports stay inside main so the Bossman Python 3.11 process never
        # needs to import OpenHands. The sidecar owns the SDK compatibility.
        from openhands.sdk import Agent, Conversation, LLM  # type: ignore
        try:
            from openhands.tools.preset.default import get_default_tools  # type: ignore
            tools = get_default_tools()
        except ImportError:
            tools = None

        model = req.get("model") or os.environ.get("BOSSMAN_OPENHANDS_MODEL") or os.environ.get("OPENHANDS_MODEL")
        if not model:
            raise ValueError("no OpenHands model configured")
        llm = LLM(model=model)
        kwargs = {"llm": llm}
        if tools is not None:
            kwargs["tools"] = tools
        agent = Agent(**kwargs)
        conversation = Conversation(agent=agent, workspace=str(workspace))
        conversation.send_message(str(req["instruction"]))
        conversation.run()
        _emit("completed", model=model)
        return 0
    except Exception as exc:  # fail closed; no traceback or secrets on stdout
        _emit("failed", error_type=type(exc).__name__, error=str(exc)[:500])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
