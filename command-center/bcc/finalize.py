"""EH-04 (TRUTH-003 §10) — ЕДИНСТВЕННАЯ точка, где задача становится `completed`.

    finalize_task(engine, run_id, task_id, answer=…, usage=…, verdicts=…)

Проверяет перед записью статуса:
  1. ни один гейт не вернул FAIL;
  2. fence движка актуален (FL-01) — зомби-воркер не финализирует;
  3. объявленные обязательные эффекты (`tasks.meta.review.evidence`,
     `tasks.meta.required_effects`) подтверждены СВЕЖИМ наблюдением пост-состояния
     (`bcc/v2/verification.verify_all`), а не ответом инструмента/модели;
  4. нет незакрытых approval'ов по вызовам инструментов этого run'а;
  5. свежесть: наблюдение сделано после последнего вызова инструмента run'а.
Отказ по объявленному эффекту — не «failed» и не «completed»: решение владельцу
(waiting_approval + review_escalation), как при упавшем гейте.
Без объявленного эффекта неуспешный изменяющий вызов завершает задачу как
failed: отсутствие контракта не делает отказ инструмента успехом задачи.

`finalize_override(svc, task_id, approval)` — решение ЧЕЛОВЕКА по review_escalation,
помечается override=True; обязательные эффекты всё равно проверяются заново.
Структурный тест `tests/test_no_direct_completed_writes.py` запрещает любую
другую запись `status="completed"` для `tasks`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timezone
import re
from typing import Any

import sqlalchemy as sa

from .db import fetch_one, tasks as tasks_t, task_runs as runs_t, tool_calls as tool_calls_t, utcnow
from .v2.verification import parse_expected, verify_all

REVIEW_KIND = "review_escalation"

#: `tasks.meta.reason_code` for a task parked because the browser shows a human
#: challenge (kept equal to review_gate.CHALLENGE_REASON_CODE; duplicated as a
#: literal so this module does not import the feature package).
CHALLENGE_REASON_CODE = "WAITING_FOR_OWNER_CHALLENGE"

# The model telling the owner to solve a challenge and press Resume. Owner audit
# 2026-09-08, task 45 (run 32): this exact prose was the `result` of a task
# recorded as completed. The regex is only ever used to REFUSE completion of a
# browser task, never to admit one, so a false match costs one owner decision
# and a miss costs nothing that the verifier does not already catch.
_CHALLENGE_EXCUSE_RE = re.compile(
    r"(?iu)(captcha|recaptcha|hcaptcha|капч\w*|verify (that )?you('re| are) (a )?human|"
    r"(вы|ты)\s+не\s+робот|подтвердит\w*,?\s+что\s+(вы|ты)\s+(не\s+робот|человек)|"
    r"(press|click|нажми\w*|нажать)\s+[«\"']?resume|cloudflare\s+challenge|"
    r"проверк\w*\s+(безопасности|человека)|human\s+verification)")


@dataclass
class FinalizeDecision:
    ok: bool
    reason: str = ""
    checks: dict[str, Any] = field(default_factory=dict)


def _required_expectations(task: dict) -> list:
    meta = dict(task.get("meta") or {})
    raw = list(((meta.get("review") or {}).get("evidence")) or []) + list(meta.get("required_effects") or [])
    expected = parse_expected(raw)
    if len(expected) != len(raw):
        raise ValueError("invalid required effect: no obligation may be discarded")
    return expected


def _effectful(row: dict) -> bool:
    """Use the executor capability, not model wording, as the safety backstop."""
    from .tools import REGISTRY
    if row.get("tool") == "terminal.run":
        from .features.action_contract import _looks_like_mutation
        command = str((row.get("args") or {}).get("command") or "")
        # Shell expansions and write-capable git subcommands must not inherit
        # the action classifier's deliberately permissive "read" heuristic.
        if re.search(r"[>$`]|\bgit\s+(?:config|branch|tag|remote|fetch)\b|--output(?:=|\s)"
                     r"|(?:^|\s)-(?:delete|exec|execdir|ok|okdir|fprint|fprint0|fprintf|fls)(?:\s|$)"
                     r"|(?:^|\s)--(?:fix(?:-only)?|pre)(?:=|\s|$)"
                     r"|\bruff\b[^;&|\n]*\bformat\b|\b(?:black|isort)\b", command):
            return True
        return _looks_like_mutation(command)
    spec = REGISTRY.get(row.get("tool", ""))
    # A tool that declares itself non-idempotent has, by its own declaration, an
    # effect worth not repeating; a "read" category label cannot make it harmless.
    return spec is None or spec.category != "read" or not getattr(spec, "idempotent", True)


# Statuses whose effect is provably absent (denied/rejected) or UNKNOWN because a
# previous attempt crashed between dispatch and receipt (interrupted/reconciled).
# With a declared contract, only a fresh observation of the world may settle them;
# without one they remain "did not succeed": an unobserved effect is not success.
_WORLD_DECIDES = ("denied", "rejected", "interrupted", "reconciled")


def _effect_problem(rows: list[dict], expected: list, task: dict | None = None) -> str:
    """Reject unsuccessful effects, then check declared capability obligations.

    Scope is deliberate. The finalizer enforces the obligations a task actually
    carries (`meta.review.evidence` / `meta.required_effects`); it does not
    invent new ones from prompt text or from the mere presence of a tool call.
    Those layers already exist and are not duplicated here:

      * `features/action_contract._gate` vetoes a CLASSIFIED action task unless
        the run holds a genuinely `executed` call of the matching non-reading
        tool family — the zero-attempt and failed-attempt cases;
      * `features/action_router` / `review_gate` attach evidence where a real
        post-state verifier is wired, and that evidence lands in `expected`.

    Re-deriving obligations here (any effectful row implies a contract; any
    classified prompt implies a contract) made every family without a wired
    verifier — apps, openclaw, opencode, plugin, mcp — unfinishable: the task
    parked in `waiting_approval` behind a `review_escalation` that no owner
    decision could ever clear, because `finalize_override` re-ran the same
    impossible check. A refusal the owner cannot resolve is not fail-closed,
    it is a dead end.

    Without a declared contract we still reject failed/denied effectful calls:
    the action classifier can miss a prompt, and model wording is not evidence
    of success (nor a reliable machine-readable refusal). The engine records
    such an unsuccessful run as failed, preserving its answer, rather than
    creating an impossible approval loop. Successful opaque capabilities need
    no invented verifier. With an explicit contract, denied calls are left to
    `verify_all`: independently observed post-state may fulfill the obligation.
    """
    # A retry of the exact action may recover a failed attempt. An unrelated
    # successful probe cannot erase a failed mutation. Read-only diagnostic
    # failures are not task failure evidence.
    latest = {}
    for row in rows:
        if _effectful(row) and not (expected and row.get("status") in _WORLD_DECIDES):
            latest[(row.get("tool"), row.get("args_hash") or repr(row.get("args")))] = row
    for row in latest.values():
        if row.get("status") != "executed" or row.get("error"):
            return "effectful tool did not succeed: " + str(row.get("tool"))
        if row.get("tool") == "terminal.run":
            match = re.match(r"exit_code=(-?\d+)\b", str(row.get("result_preview") or ""))
            if match is None or int(match[1]) != 0:
                return "effectful terminal outcome is failed or still unobserved"
    if not expected:
        return ""
    # What a capability is known to be able to leave behind. The point is to stop
    # an unrelated capability's success from being credited against somebody
    # else's obligation — a browser click cannot be the proof that a process is
    # running. It is NOT a list of capabilities allowed to finish.
    #
    # Two corrections, both from real dead ends. `memory.write` writes a markdown
    # note into the vault, so a `file` obligation over that note is exactly the
    # right way to check it — mapping memory to {memory, db} refused a run whose
    # effect had genuinely happened and which `verify_all` could have confirmed
    # by reading the file. And an UNKNOWN capability is not a known mismatch: mcp,
    # plugin and openclaw tools can do anything the server behind them does, so
    # refusing them here parked them behind an escalation `finalize_override`
    # re-refused forever. Absence of knowledge is not evidence of mismatch; when
    # we cannot say the capability is wrong, `verify_all` reads the world and
    # answers instead.
    kinds = {e.kind for e in expected}
    known_kinds = {"terminal": {"file", "terminal", "github"}, "browser": {"browser", "file"},
                   "memory": {"memory", "db", "file"}, "apps": {"app", "process", "file"},
                   "opencode": {"file", "github"}}
    for row in latest.values():
        supported = known_kinds.get(row.get("source"))
        if supported is not None and not kinds.intersection(supported):
            return "effectful capability has no matching post-state verifier: " + str(row.get("tool"))
    return ""


def _executed_effect(rows: list[dict]) -> bool:
    """At least one effectful tool call this run actually executed."""
    return any(r.get("status") == "executed" and _effectful(r) for r in rows)


def _browser_task(task: dict, rows: list[dict], expected: list, session_id: int | None) -> bool:
    """Is the browser part of what this task does? Any one signal suffices: a
    browser tool row, a browser expectation, a live browser session bound to
    the task, or the action router having granted browser tools for it."""
    if session_id is not None:
        return True
    if any(str(r.get("source") or "") == "browser" or str(r.get("tool") or "").startswith("browser.")
           for r in rows):
        return True
    if any(getattr(e, "kind", "") == "browser" for e in expected):
        return True
    meta = task.get("meta") or {}
    router = meta.get("action_router") if isinstance(meta.get("action_router"), dict) else {}
    if router.get("capability") == "BROWSER_ACTION":
        return True
    return any(str(t).startswith("browser.") for t in (meta.get("allowed_tools") or []))


async def _task_browser_session(svc, task: dict) -> int | None:
    try:
        from .v2.tables import browser_sessions as bs_t
        async with svc.db.session() as s:
            row = (await s.execute(sa.select(bs_t.c.id).where(sa.and_(
                bs_t.c.task_id == task.get("id"), bs_t.c.status == "running"))
                .order_by(bs_t.c.id.desc()).limit(1))).first()
        return int(row._mapping["id"]) if row is not None else None
    except Exception:  # noqa: BLE001
        return None


async def _live_browser_challenge(svc, session_id: int | None) -> str:
    """Provider name of a human challenge on the task's live browser session, or "".

    Independent of declared expectations: a browser task without a derivable
    domain has no `review.evidence`, and without this check "complete the
    CAPTCHA" plus one executed `browser.open` finalized as completed."""
    if session_id is None:
        return ""
    try:
        from .features.browser import _mgr as _bmgr
        snap = await _bmgr(svc).snapshot(int(session_id), actor="verifier", approved=True)
        captcha = getattr(snap, "captcha", None) or (snap if isinstance(snap, dict) else {}).get("captcha") or {}
        if isinstance(captcha, dict) and captcha.get("present"):
            return str(captcha.get("provider") or "проверка человека")
    except Exception:  # noqa: BLE001 — cannot observe → not a challenge verdict either way
        return ""
    return ""


async def finalize_task(engine, run_id: int, task_id: int, *, answer: str, usage: dict[str, Any],
                        verdicts: list[Any] | None = None) -> FinalizeDecision:
    svc = engine.services
    checks: dict[str, Any] = {"verdicts_fail": 0, "expectations": 0, "verification": "NOT_REQUIRED",
                              "open_approvals": 0, "fresh": True}
    for res in verdicts or []:
        if isinstance(res, dict) and str(res.get("verdict", "")).upper() == "FAIL":
            checks["verdicts_fail"] += 1
    if checks["verdicts_fail"]:
        return FinalizeDecision(False, "gate verdict FAIL", checks)

    # 2. fence — FencedOut пробрасывается: зомби ничего не пишет (обрабатывает execute())
    await engine.assert_fence(run_id)

    async with engine.db.session() as s:
        task = await fetch_one(s, tasks_t, task_id) or {}
        run = await fetch_one(s, runs_t, run_id)
        if not task or not run or run["task_id"] != task_id:
            return FinalizeDecision(False, "run does not belong to this task", checks)
        rows = [dict(r._mapping) for r in (await s.execute(sa.select(tool_calls_t).where(
            tool_calls_t.c.run_id == run_id).order_by(tool_calls_t.c.id))).fetchall()]
    checks["open_approvals"] = sum(1 for r in rows if r["status"] in ("pending_approval", "approved"))
    if checks["open_approvals"]:
        return FinalizeDecision(False, "tool call still waiting for approval", checks)
    last_effect = max((r["finished_at"] for r in rows if r["finished_at"] is not None), default=None)

    try:
        expected = _required_expectations(task)
    except (TypeError, ValueError):
        return FinalizeDecision(False, "invalid required effects contract", checks)
    problem = _effect_problem(rows, expected, task)
    if problem:
        if not expected:
            checks["failure_status"] = "failed"
        return FinalizeDecision(False, problem, checks)
    checks["expectations"] = len(expected)

    # A human challenge is a STATE, never a success. Checked before the declared
    # expectations so a browser task with no derivable domain (hence no
    # `review.evidence`) is caught too. The owner clears the page and presses
    # Resume; the run continues from its checkpoint and is judged again.
    session_id = await _task_browser_session(svc, task)
    if _browser_task(task, rows, expected, session_id):
        provider = await _live_browser_challenge(svc, session_id)
        if provider:
            checks["failure_status"] = "paused"
            checks["reason_code"] = CHALLENGE_REASON_CODE
            return FinalizeDecision(False, f"BROWSER_CHALLENGE: на странице проверка человека ({provider}) — "
                                           "цель не достигнута; пройдите проверку и нажмите Resume", checks)
        if _CHALLENGE_EXCUSE_RE.search(str(answer or "")):
            # No live challenge observed, but the model's own result is an
            # instruction to the owner to solve one. That text is not the goal.
            checks["failure_status"] = "paused"
            checks["reason_code"] = CHALLENGE_REASON_CODE
            return FinalizeDecision(False, "BROWSER_CHALLENGE: ответ модели — просьба к владельцу пройти "
                                           "проверку на странице, а не достигнутая цель", checks)

    if expected:
        import time as _time
        t_verify = _time.monotonic()
        try:
            from .features.tools_terminal import _roots
            roots = await _roots(svc) if svc is not None else []
        except Exception:  # noqa: BLE001
            roots = []
        await engine.bus.emit("observation.started", task_id=task_id, run_id=run_id, expectations=len(expected))
        status, reason, results = await verify_all(expected, svc=svc, task=task, roots=roots)
        checks["verification"] = status
        checks["verification_ms"] = int((_time.monotonic() - t_verify) * 1000)
        await engine.bus.emit("verification.result", task_id=task_id, run_id=run_id, status=status,
                              reason=reason[:300], verification_ms=checks["verification_ms"])
        checks["verification_reason"] = reason[:300]
        observed = [r.observed.observed_at for r in results if r.observed is not None]
        if last_effect is not None and observed and min(observed) < last_effect.replace(tzinfo=timezone.utc).timestamp():
            checks["fresh"] = False
            return FinalizeDecision(False, "STALE_EVIDENCE_REJECTED: observation predates the last tool effect", checks)
        if status == "BLOCKED":
            checks["failure_status"] = "paused"
            checks["reason_code"] = CHALLENGE_REASON_CODE
            return FinalizeDecision(False, f"BROWSER_CHALLENGE: {reason}", checks)
        if status != "VERIFIED":
            return FinalizeDecision(False, f"required effects not verified: {reason}", checks)
    elif not str(answer or "").strip() and not _executed_effect(rows):
        # Nothing was delivered: no text, no declared-and-verified effect, no
        # executed effectful tool call. Owner audit 2026-09-08, tasks 22 and 44:
        # `result=""` recorded as completed and billed. This is deliberately
        # NOT a global "answer must be non-empty" rule — an effect task whose
        # declared effect verified above completes with an empty answer, and a
        # run whose effectful tool executed keeps its existing contract; only
        # the empty-handed run is refused.
        checks["failure_status"] = "failed"
        return FinalizeDecision(False, "EMPTY_RESULT: модель не вернула ни текста, ни проверенного "
                                       "эффекта — пустой ответ не является результатом", checks)

    await engine._finish(run_id, task_id, "completed", result=answer, **usage)
    await engine.bus.emit("task.finalized", task_id=task_id, run_id=run_id, checks=checks, override=False)
    return FinalizeDecision(True, "finalized", checks)


async def _override_reason(svc, task: dict, rows: list[dict]) -> str:
    """Why a human override may NOT finalize this task — "" if nothing blocks it.

    Shared by `finalize_override` (which acts on it) and
    `finalize_decision_reason` (which only reports it, so the owner's next
    escalation question can name the real blocker instead of repeating the
    first ask). Keeping one implementation is the point: a diagnostic that
    disagreed with the gate would be worse than none."""
    try:
        expected = _required_expectations(task)
        reason = _effect_problem(rows, expected, task)
        if any(r["status"] in ("pending_approval", "approved") for r in rows):
            reason = "tool call still waiting for approval"
        if expected and not reason:
            from .features.tools_terminal import _roots
            status, reason, results = await verify_all(expected, svc=svc, task=task, roots=await _roots(svc))
            if status == "VERIFIED":
                reason = ""
                finished = [r["finished_at"].replace(tzinfo=timezone.utc).timestamp()
                            for r in rows if r["finished_at"] is not None]
                observed = [r.observed.observed_at for r in results if r.observed is not None]
                if finished and (not observed or min(observed) < max(finished)):
                    reason = "STALE_EVIDENCE_REJECTED"
    except Exception as exc:  # observation unavailable is not approval evidence
        reason = "required effect verification unavailable: " + type(exc).__name__
    return reason or ""


async def _run_tool_rows(svc, run_id) -> list[dict]:
    if run_id is None:
        return []
    async with svc.db.session() as s:
        return [dict(r._mapping) for r in (await s.execute(sa.select(tool_calls_t).where(
            tool_calls_t.c.run_id == run_id).order_by(tool_calls_t.c.id))).fetchall()]


async def finalize_decision_reason(svc, task_id: int, run_id=None) -> str:
    """Report-only twin of `_override_reason`: never writes, never finalizes.

    Used by the review-escalation state machine to explain a refusal. Returns
    "" when nothing blocks finalization (which itself is worth reporting — it
    means the refusal came from a precondition, not from the evidence)."""
    async with svc.db.session() as s:
        task = await fetch_one(s, tasks_t, task_id)
    if not task:
        return "task not found"
    return await _override_reason(svc, task, await _run_tool_rows(svc, run_id))


async def finalize_override(svc, task_id: int, *, approval: dict) -> bool:
    """Human review can waive reviewer judgement, never required world evidence."""
    from .db import approvals as approvals_t
    async with svc.db.session() as s:
        task = await fetch_one(s, tasks_t, task_id)
        authorized = await fetch_one(s, approvals_t, approval.get("id"))
        latest_run = (await s.execute(sa.select(runs_t.c.id).where(
            runs_t.c.task_id == task_id).order_by(runs_t.c.id.desc()).limit(1))).scalar()
        if (not task or task["status"] != "waiting_approval" or not authorized
                or authorized["task_id"] != task_id or authorized["status"] != "approved"
                or authorized["kind"] not in (REVIEW_KIND, "review_escalation_done")
                or authorized["run_id"] != approval.get("run_id")
                or latest_run != authorized["run_id"]):
            return False
        rows = [dict(r._mapping) for r in (await s.execute(sa.select(tool_calls_t).where(
            tool_calls_t.c.run_id == authorized["run_id"]).order_by(tool_calls_t.c.id))).fetchall()]
    reason = await _override_reason(svc, task, rows)
    if reason:
        await svc.bus.emit("task.finalize_refused", task_id=task_id, run_id=authorized["run_id"],
                           reason=reason, override=True)
        return False
    async with svc.db.session() as s:
        t = (await s.execute(sa.select(tasks_t.c.status).where(tasks_t.c.id == task_id))).first()
        if not t or t._mapping["status"] in ("completed", "cancelled"):
            return False
        result = await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id,
                                                        tasks_t.c.status == "waiting_approval").values(
            status="completed", updated_at=utcnow()))
        await s.commit()
        if result.rowcount != 1:
            return False
    await svc.bus.emit("task.finalized", task_id=task_id, run_id=approval.get("run_id"), override=True,
                       approval_id=approval.get("id"), decided_by=approval.get("decided_by"))
    await svc.bus.emit("task.completed", task_id=task_id, run_id=approval.get("run_id"), override=True)
    return True
