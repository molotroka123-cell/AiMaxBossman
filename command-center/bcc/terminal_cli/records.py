"""Machine contract of the terminal: backend events -> versioned records.

One JSON object per line (`--output-format stream-json`, `bossman exec`,
`bossman events`). The human view renders the SAME records, so what Claude
Code reads and what the owner sees cannot drift apart.

Schema `bossman.events.v1` — every record has "v": 1 and "type":

  system/init        connection, backend build, client, agent, model (+model_kind)
  task               submitted | replayed | status   (task_id, run_id, status)
  step               task.progress: step/max_steps/model/tool_calls
  thinking           ONLY reasoning the provider actually returned (never made up)
  assistant          text that accompanied tool calls in a step
  assistant_message  the model's final answer text of a step
  tool_use           id, name, input (redacted by the backend), step
  tool_result        tool_use_id, name, is_error, summary, content (preview), duration_ms
  tool_denied        name, reason (policy refused; nothing executed)
  approval_required  approval_id, kind, preview — never auto-approved
  approval_decided   approval_id, status, by
  memory             recalled | skipped | fact_added
  skill              skills the backend reports as used
  usage              measured tokens / cost (cost only when pricing is known)
  evaluation         verifier/gate verdict
  fallback           router/model fallback
  log                run log line (warnings/errors; all with --verbose)
  stream             reconnected | disconnected | lagged
  result             ok (operation), task_state, exit_code, …   (always last)

Text fields are neutralised (console.sanitize) and bounded (MAX_TEXT).
"""
from __future__ import annotations

from typing import Any

from .console import sanitize

SCHEMA = "bossman.events.v1"
VERSION = 1
MAX_TEXT = 20_000

# --- exit codes: operation outcome and task state, documented in TERMINAL.md --
EXIT_OK = 0              # PASS (or: operation succeeded, e.g. submitted with --detach)
EXIT_FAIL = 1            # FAIL: the task failed
EXIT_USAGE = 2           # bad arguments / input schema
EXIT_DISCONNECTED = 3    # backend unreachable, auth refused, stream lost with unknown state
EXIT_WAIT_APPROVAL = 4   # --approval-mode fail and the task waits for an owner decision
EXIT_BLOCKED = 5         # task refused/blocked at admission (no executor/capability)
EXIT_STOPPED = 6         # task stopped (owner STOP, `bossman stop`, Ctrl+C)
EXIT_TIMEOUT = 7         # --max-seconds elapsed
EXIT_PARTIAL = 8         # task paused / waits for the owner outside an approval
EXIT_NOT_FOUND = 9       # no such task/run/approval/provider
EXIT_NOT_SUPPORTED = 10  # endpoint absent in this build (e.g. /api/evolution)
EXIT_CONFLICT = 11       # state conflict (already decided / already finished)
EXIT_INTERRUPTED = 130   # Ctrl+C in the client

TASK_STATES = ("PASS", "FAIL", "PARTIAL", "BLOCKED", "WAIT_APPROVAL", "TIMEOUT",
               "DISCONNECTED", "STOPPED", "RUNNING", "INTERRUPTED")
STATE_EXIT = {"PASS": EXIT_OK, "FAIL": EXIT_FAIL, "PARTIAL": EXIT_PARTIAL,
              "BLOCKED": EXIT_BLOCKED, "WAIT_APPROVAL": EXIT_WAIT_APPROVAL,
              "TIMEOUT": EXIT_TIMEOUT, "DISCONNECTED": EXIT_DISCONNECTED,
              "STOPPED": EXIT_STOPPED, "RUNNING": EXIT_OK, "INTERRUPTED": EXIT_INTERRUPTED}

TERMINAL_STATUSES = ("completed", "failed", "stopped", "cancelled", "blocked")

#: run.log kinds that matter to a reader; the rest only with --verbose.
NOTABLE_LOG = {
    "run.error", "run.failed", "run.recovery_exhausted", "run.budget_stop", "run.review_fail",
    "run.finalize_refused", "run.waiting_for_owner", "run.gate_failed", "run.stopped",
    "run.paused", "run.deferred", "run.provenance_not_captured", "tool.denied", "tool.ask",
    "tool.rejected", "tool.rejected_repeat", "tool.replay_guard", "tool.ambiguous_effect",
    "tool.lease_used", "router.fallback", "model.fallback", "recovery.alternate_failed",
    "memory.recalled", "memory.recall_skipped",
}


def state_of(status: str | None) -> str:
    """Backend task status -> task_state of the contract."""
    s = str(status or "")
    if s == "completed":
        return "PASS"
    if s == "failed":
        return "FAIL"
    if s in ("stopped", "cancelled"):
        return "STOPPED"
    if s == "blocked":
        return "BLOCKED"
    if s == "waiting_approval":
        return "WAIT_APPROVAL"
    if s == "paused":
        return "PARTIAL"
    return "RUNNING"


def is_mock_model(name: str | None) -> bool:
    return "DETERMINISTIC-TEST-MODEL" in str(name or "")


def model_kind(name: str | None) -> str:
    """MOCK_MODEL for the deterministic scripted test model — shown, never hidden."""
    return "MOCK_MODEL" if is_mock_model(name) else "REAL_MODEL"


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    s = sanitize(value)
    return s if len(s) <= limit else s[:limit] + f"…[+{len(s) - limit}]"


def _clean(value: Any, depth: int = 0) -> Any:
    """Sanitise every string inside a JSON-like value (tool arguments)."""
    if depth > 6:
        return "…"
    if isinstance(value, str):
        return _text(value, 4000)
    if isinstance(value, dict):
        return {sanitize(k): _clean(v, depth + 1) for k, v in list(value.items())[:64]}
    if isinstance(value, (list, tuple)):
        return [_clean(v, depth + 1) for v in list(value)[:64]]
    return value


def record(type_: str, **fields: Any) -> dict:
    return {"v": VERSION, "type": type_, **{k: v for k, v in fields.items() if v is not None}}


def normalize(msg: dict, *, verbose: bool = False) -> list[dict]:
    """One backend event -> zero or more contract records. Pure, never raises."""
    try:
        return _normalize(msg, verbose=verbose)
    except Exception as exc:  # noqa: BLE001 — неизвестная форма события не роняет клиента
        return [record("log", level="warn", kind="client.unparsed_event",
                       message=f"{type(exc).__name__}: {sanitize(str(msg.get('kind')))}")]


def _normalize(msg: dict, *, verbose: bool) -> list[dict]:
    kind = str(msg.get("kind") or "")
    common = {"seq": msg.get("seq") if isinstance(msg.get("seq"), int) else None,
              "ts": msg.get("ts")}
    tid, rid = msg.get("task_id"), msg.get("run_id")
    if kind in ("task.created", "task.queued", "task.started", "task.paused", "task.stopped",
                "task.completed", "task.failed", "task.blocked"):
        status = {"task.created": "draft", "task.queued": "queued", "task.started": "running",
                  "task.paused": "paused", "task.stopped": "stopped", "task.completed": "completed",
                  "task.failed": "failed", "task.blocked": "blocked"}[kind]
        extra = {}
        if msg.get("error"):
            extra["error"] = _text(msg["error"], 2000)
        if msg.get("reason"):
            extra["reason"] = _text(msg["reason"], 2000)
        if msg.get("approval_resumed"):
            extra["approval_resumed"] = True
        return [record("task", subtype="status", task_id=tid, run_id=rid, status=status,
                       **extra, **common)]
    if kind == "task.progress":
        if msg.get("waiting_approval"):
            return [record("task", subtype="status", task_id=tid, run_id=rid,
                           status="waiting_approval", tool=msg.get("tool"), **common)]
        return [record("step", task_id=tid, run_id=rid, step=msg.get("step"),
                       max_steps=msg.get("max_steps"), model=_text(msg.get("model"), 200) or None,
                       tool_calls=[sanitize(t) for t in msg.get("tool_calls") or []] or None,
                       **common)]
    if kind == "run.reasoning_delta":
        return [record("thinking", delta=_text(msg.get("text")), step=msg.get("step"),
                       source=msg.get("source") or "provider_message", run_id=rid, **common)]
    if kind == "run.assistant_delta":
        return [record("assistant", delta=_text(msg.get("text")), step=msg.get("step"),
                       run_id=rid, **common)]
    if kind == "run.assistant_message":
        return [record("assistant_message", text=_text(msg.get("text")), step=msg.get("step"),
                       model=_text(msg.get("model"), 200) or None, run_id=rid, **common)]
    if kind == "run.tool_use":
        return [record("tool_use", id=sanitize(msg.get("call_id")), name=sanitize(msg.get("tool")),
                       input=_clean(msg.get("args") or {}), step=msg.get("step"),
                       source=sanitize(msg.get("source")) or None, run_id=rid, **common)]
    if kind == "run.tool_result":
        return [record("tool_result", tool_use_id=sanitize(msg.get("call_id")),
                       name=sanitize(msg.get("tool")), is_error=not bool(msg.get("ok")),
                       summary=_text(msg.get("summary"), 500), content=_text(msg.get("preview"), 4000),
                       truncated=bool(msg.get("truncated")), duration_ms=msg.get("duration_ms"),
                       step=msg.get("step"), run_id=rid, **common)]
    if kind == "tool.denied":
        return [record("tool_denied", name=sanitize(msg.get("tool")),
                       reason=_text(msg.get("reason"), 1000), run_id=rid, **common)]
    if kind == "tool.replayed":
        return [record("log", level="warn", kind="tool.replayed",
                       message=f"{sanitize(msg.get('tool'))}: шаг уже исполнен прежней попыткой — "
                               f"не повторяется", run_id=rid, **common)]
    if kind == "approval.created":
        return [record("approval_required", approval_id=msg.get("id"),
                       kind=sanitize(msg.get("approval_kind")) or "tool",
                       preview=_text(msg.get("preview"), 4000), task_id=tid, run_id=rid, **common)]
    if kind == "approval.decided":
        return [record("approval_decided", approval_id=msg.get("id"),
                       status=sanitize(msg.get("status")), by=sanitize(msg.get("by")) or None,
                       **common)]
    if kind in ("approval.deduplicated", "approval.repeat_suppressed", "approval.budget_exceeded"):
        return [record("log", level="warn", kind=kind,
                       message=_text(f"{kind}: {msg.get('tool') or ''}", 500), **common)]
    if kind == "run.usage":
        cost_known = bool(msg.get("pricing_known"))
        return [record("usage", step=msg.get("step"), model=_text(msg.get("model"), 200) or None,
                       reported=bool(msg.get("usage_reported")),
                       step_tokens_in=msg.get("step_tokens_in"), step_tokens_out=msg.get("step_tokens_out"),
                       tokens_in=msg.get("tokens_in"), tokens_out=msg.get("tokens_out"),
                       cost_usd=msg.get("cost_usd") if cost_known else None, cost_known=cost_known,
                       context_window=msg.get("context_window"),
                       max_tokens_total=msg.get("max_tokens_total"),
                       max_cost_usd=msg.get("max_cost_usd"), run_id=rid, **common)]
    if kind == "memory.fact.added":
        return [record("memory", subtype="fact_added",
                       message=_text(f"факт #{msg.get('fact_id')}: {msg.get('subject')} "
                                     f"{msg.get('predicate')}", 500), run_id=rid, **common)]
    if kind in ("skill.run", "skill.finished"):
        return [record("skill", subtype=kind.split(".", 1)[1], ids=[sanitize(msg.get("slug"))],
                       task_id=tid, **common)]
    if kind == "evaluation.completed":
        return [record("evaluation", verdict=sanitize(msg.get("verdict")),
                       reasons=_text(msg.get("reasons"), 1000), run_id=rid, **common)]
    if kind in ("router.fallback", "model.status"):
        return [record("fallback", what=kind.split(".")[0],
                       detail=_text(msg.get("reason") or msg.get("detail"), 500), **common)]
    if kind == "run.budget_exceeded":
        return [record("log", level="error", kind=kind,
                       message=_text(msg.get("reason") or msg.get("breach") or "бюджет исчерпан", 500),
                       run_id=rid, **common)]
    if kind == "run.log":
        log_kind = str(msg.get("log_kind") or "")
        text = _text(msg.get("message"), 2000)
        if log_kind == "memory.recalled":
            count = None
            digits = "".join(ch for ch in text.split("записей")[0] if ch.isdigit())
            if digits:
                count = int(digits)
            return [record("memory", subtype="recalled", message=text, count=count, run_id=rid,
                           **common)]
        if log_kind == "memory.recall_skipped":
            return [record("memory", subtype="skipped", message=text, run_id=rid, **common)]
        if not verbose and log_kind not in NOTABLE_LOG and msg.get("level") not in ("warn", "error"):
            return []
        return [record("log", level=sanitize(msg.get("level")) or "info", kind=sanitize(log_kind),
                       message=text, run_id=rid, **common)]
    if kind in ("stream.reconnected", "stream.disconnected", "stream.lagged", "stream.error"):
        return [record("stream", subtype=kind.split(".", 1)[1], cursor=msg.get("cursor"),
                       message=_text(msg.get("message"), 500) or None)]
    if kind.startswith("coding.task."):
        return [record("log", level="info", kind=kind, message=_text(msg.get("error") or kind, 500),
                       **common)]
    if verbose and kind and kind not in ("stream.keepalive", "stream.open", "stream.replayed",
                                         "checkpoint.created", "cache.observation"):
        return [record("log", level="debug", kind=sanitize(kind), message="", **common)]
    return []
