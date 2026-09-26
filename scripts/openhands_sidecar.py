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
import time
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
    instruction = str(req["instruction"])
    # Uniform environment note for EVERY run (baseline and candidate alike):
    # the terminal is Git Bash on Windows, so %VAR% never expands and Windows
    # paths must stay inside the workspace. Fair comparison, no hidden hints.
    instruction = ("Terminal: Git Bash on Windows. Use POSIX syntax; %VAR% is "
                   "NOT expanded and must not appear in commands. Stay inside "
                   "the workspace directory.\n\n" + instruction)
    max_iterations = int(req.get("max_iterations") or 30)
    started_at = time.time()
    calls: list[dict] = []

    def _on_event(event) -> None:
        # Per-call telemetry for the lab's fairness/looping detectors: tool
        # name + monotonic offset only; no arguments, no results, no secrets.
        try:
            from openhands.sdk.event import ActionEvent
            if isinstance(event, ActionEvent):
                name = str(getattr(event, "tool_name", "")
                           or getattr(getattr(event, "action", None), "tool_name", ""))
                calls.append({"tool": name, "t": round(time.time() - started_at, 3)})
        except Exception:  # noqa: BLE001 — telemetry must never break the run
            pass

    # A mid-conversation serialization hiccup (model response shape the SDK's
    # pydantic models reject) must not fail the whole task: one fresh retry
    # with the SAME instruction in the SAME workspace.
    for attempt in (1, 2):
        try:
            conversation = Conversation(agent=agent, workspace=str(workspace),
                                        visualizer=None,
                                        max_iteration_per_run=max_iterations,
                                        callbacks=[_on_event])
            conversation.send_message(instruction)
            conversation.run()
            _emit(out, "completed", model=str(model),
                  tool_calls=calls, tool_calls_total=len(calls),
                  elapsed_seconds=round(time.time() - started_at, 1))
            return 0
        except Exception as exc:  # noqa: BLE001
            if attempt == 2:
                raise
            print(f"sidecar: retrying after {type(exc).__name__}: {str(exc)[:120]}",
                  file=sys.stderr, flush=True)


def _sanitize(obj):
    """Strip lone surrogates: the SDK's pydantic serialization refuses them."""
    if isinstance(obj, str):
        return obj.encode("utf-8", "replace").decode("utf-8")
    if isinstance(obj, dict):
        return {_sanitize(k): _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


def main() -> int:
    with _stdout_reserved() as out:
        try:
            req = _sanitize(json.load(sys.stdin))
        except Exception as exc:  # noqa: BLE001 — тип наружу, детали в stderr
            _emit(out, "failed", error_type=type(exc).__name__)
            return 1
        if isinstance(req, dict) and req.get("op") == "handshake":
            # Readiness is a probe, not a configured string: the SDK must import
            # and a provider key must be present, or this sidecar cannot run.
            import importlib.util
            sdk = importlib.util.find_spec("openhands") is not None
            key = bool(os.environ.get("OPENROUTER_API_KEY") or os.environ.get("LLM_API_KEY"))
            ready = sdk and key
            _emit(out, "ready" if ready else "failed", executor="openhands-sdk", tools=["terminal", "file_editor"],
                  error="" if ready else ("openhands-sdk not installed" if not sdk else "OpenRouter API key required"))
            return 0 if ready else 1
        try:
            return _run(req, out)
        except Exception as exc:  # noqa: BLE001 — сообщение может нести путь/секрет
            _emit(out, "failed", error_type=type(exc).__name__)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
