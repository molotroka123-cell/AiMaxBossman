"""OpenHands SDK sidecar for Bossman (Python 3.12+ runtime).

One request arrives on stdin and one typed response leaves on stdout. The
sidecar never prints provider credentials, raw model reasoning or a traceback.
Use an OpenRouter model id and pass OPENROUTER_API_KEY explicitly from Bossman's
guarded builder.
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
        if not workspace.is_dir() or not (workspace / ".git").exists():
            raise ValueError("workspace must be a git checkout")
        model = req.get("model") or os.environ.get("BOSSMAN_OPENHANDS_MODEL") or os.environ.get("OPENHANDS_MODEL")
        if not model or not str(model).startswith("openrouter/"):
            raise ValueError("OpenRouter model required")

        api_key = os.environ.pop("OPENROUTER_API_KEY", None) or os.environ.pop("LLM_API_KEY", None)
        base_url = os.environ.pop("OPENROUTER_BASE_URL", None) or os.environ.pop("LLM_BASE_URL", None)
        if not api_key:
            raise ValueError("OpenRouter API key required")

        from openhands.sdk import Agent, Conversation, LLM, Tool  # type: ignore
        from openhands.tools.file_editor import FileEditorTool  # type: ignore
        from openhands.tools.task_tracker import TaskTrackerTool  # type: ignore
        from openhands.tools.terminal import TerminalTool  # type: ignore

        llm = LLM(model=str(model), api_key=api_key, base_url=base_url or None)
        agent = Agent(llm=llm, tools=[
            Tool(name=TerminalTool.name),
            Tool(name=FileEditorTool.name),
            Tool(name=TaskTrackerTool.name),
        ])
        conversation = Conversation(agent=agent, workspace=str(workspace))
        conversation.send_message(str(req["instruction"]))
        conversation.run()
        _emit("completed", model=str(model))
        return 0
    except Exception as exc:
        _emit("failed", error_type=type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
