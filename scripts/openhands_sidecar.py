"""OpenHands SDK sidecar for Bossman (Python 3.12+ runtime).

One request arrives on stdin and one typed response leaves on stdout. The
sidecar never prints provider credentials, raw model reasoning or a traceback.
Use an OpenRouter model id and pass OPENROUTER_API_KEY explicitly from Bossman's
guarded builder.

Keeping that promise takes explicit work, and running the real package proved
it. `openhands-sdk` attaches a conversation visualizer that renders the system
prompt, every model turn and every tool result to STDOUT, and prints a startup
banner besides. Two consequences, both real:

  * the JSON protocol breaks — `OpenHandsClient` parses stdout and got a system
    prompt instead of a response object;
  * raw model reasoning leaks into whatever captures that stream, which is
    exactly what the paragraph above says this sidecar does not do.

So stdout is claimed before the SDK is imported: file descriptor 1 is dup'd
aside for the single response line and then pointed at stderr, so anything the
SDK writes — through Python, through `rich`, or straight to the fd — lands on
the diagnostic stream where it belongs. The visualizer is disabled as well;
the redirect is the guarantee, and disabling it is the tidy default.
"""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import sys

SCHEMA = "bossman.openhands.v1"


@contextlib.contextmanager
def _stdout_reserved():
    """Hand back a writable copy of the real stdout and point fd 1 at stderr.

    Redirecting `sys.stdout` alone would not be enough: `rich` and any native
    writer address file descriptor 1 directly."""
    saved_fd = os.dup(1)
    try:
        os.dup2(2, 1)
        sys.stdout = os.fdopen(os.dup(1), "w", encoding="utf-8", errors="replace")
        with os.fdopen(saved_fd, "w", encoding="utf-8", errors="replace") as real:
            yield real
    finally:
        with contextlib.suppress(Exception):
            sys.stdout.flush()


def _emit(out, status: str, **extra: object) -> None:
    out.write(json.dumps({"schema": SCHEMA, "status": status, **extra},
                         separators=(",", ":")) + "\n")
    out.flush()


def _run(req: dict, out) -> int:
    if req.get("schema") != SCHEMA:
        raise ValueError("unsupported schema")
    workspace = Path(req["workspace"]).resolve()
    if not workspace.is_dir() or not (workspace / ".git").exists():
        raise ValueError("workspace must be a git checkout")
    model = (req.get("model") or os.environ.get("BOSSMAN_OPENHANDS_MODEL")
             or os.environ.get("OPENHANDS_MODEL"))
    if not model or not str(model).startswith("openrouter/"):
        raise ValueError("OpenRouter model required")

    # Popped, not read: the key must not remain in the environment the agent's
    # own terminal tool inherits.
    api_key = os.environ.pop("OPENROUTER_API_KEY", None) or os.environ.pop("LLM_API_KEY", None)
    base_url = os.environ.pop("OPENROUTER_BASE_URL", None) or os.environ.pop("LLM_BASE_URL", None)
    if not api_key:
        raise ValueError("OpenRouter API key required")

    os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")
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
    conversation = Conversation(agent=agent, workspace=str(workspace),
                                visualizer=None,
                                max_iteration_per_run=int(req.get("max_iterations") or 30))
    conversation.send_message(str(req["instruction"]))
    conversation.run()
    _emit(out, "completed", model=str(model))
    return 0


def main() -> int:
    with _stdout_reserved() as out:
        try:
            req = json.load(sys.stdin)
        except Exception as exc:  # noqa: BLE001 — тип наружу, детали в stderr
            _emit(out, "failed", error_type=type(exc).__name__)
            return 1
        try:
            return _run(req, out)
        except Exception as exc:  # noqa: BLE001 — сообщение может нести путь/секрет
            _emit(out, "failed", error_type=type(exc).__name__)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
