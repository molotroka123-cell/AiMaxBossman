"""Feature — the execution plan behind the assistant panel, and the capability
surface the owner can read.

This module answers three owner questions with one source of truth each, and it
answers them without ever becoming a second finalizer:

  * "what is this task actually doing?"  -> `plan()` over tasks / task_runs /
    tool_calls / approvals / the finalizer's own `verification.result` event;
  * "what can this build do at all?"     -> `bcc.capability.surface()` over the
    live tool registry, plus the capabilities that `features.action_contract`
    declares but no executor serves;
  * "do that for me"                     -> `QUICK_ACTIONS`, each of which names
    its capability and its expected post-state, so a quick action is finished by
    exactly the same `finalize` path as anything else.

WHAT THIS MODULE MUST NEVER DO
------------------------------
Nothing here writes a task status, and nothing here decides that work is done.
`bcc/finalize.py` remains the only place a task becomes `completed`; this module
only READS what that machinery already recorded. If the plan and the finalizer
ever disagree, the plan is wrong by construction, because every fact in it comes
from a row the executor or the finalizer wrote.

REPORTED IS NOT VERIFIED
------------------------
The distinction the whole panel exists for. A `tool_calls` row with
`status="executed"` is the EXECUTOR's claim that its call returned. It is not
evidence that the world changed. The only thing in this repository that reads
the world back is `bcc/v2/verification.verify_all`, run by the finalizer, whose
outcome is recorded as the `verification.result` event.

So a step is drawn with a filled check (`MARK_VERIFIED`) only when ALL of:

  1. the step carries a DECLARED obligation — an entry of
     `tasks.meta.review.evidence` / `tasks.meta.required_effects`, parsed by the
     finalizer's own `parse_expected`. Obligations are never invented here: a
     step with no declared post-state can never be more than "reported";
  2. the finalizer's `verification.result` for this run says `VERIFIED`.
     `verify_all` aggregates to VERIFIED only when EVERY expectation verified,
     so a run-level VERIFIED does entail this obligation was observed;
  3. that observation is FRESH for this step — the event is not older than the
     step's `finished_at`, the same staleness rule `finalize_task` applies.

Anything else is `MARK_REPORTED`, the hollow ring. Where the two cannot be told
apart, the hollow ring is what is drawn — that is the deliberate direction of
error, and `MARK_VERIFIED` is unreachable except through the three conditions
above.

Attaching an obligation to a step is deterministic and deliberately narrow: the
obligation's `target` must appear literally in the call's arguments, and it is
attached to the LAST such call only. Two calls naming the same file would
otherwise both claim the single proof that only covers the last one. No match,
no attachment, hollow ring — the plan says "I cannot tell" by drawing the weaker
mark, never by guessing.

One coupling is stated openly: `terminal.run` records its exit code in
`result_preview`, and a non-zero exit is drawn as failed even though the call's
status is `executed` — the same reading `finalize._effect_problem` performs. It
can only ever DOWNGRADE a mark (executed -> failed), never upgrade one, so a
drift between the two readings can never manufacture a verified step.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import capability as cap_mod
from ..db import approvals as approvals_t, events as events_t, fetch_one
from ..db import task_runs as runs_t, tasks as tasks_t, tool_calls as tool_calls_t, utcnow
from ..plugin_security import redact, redact_text
from ..tools import REGISTRY
from ..v2.verification import ExpectedState, KINDS, parse_expected, payload_digest
from . import Feature

router = APIRouter()

# ------------------------------------------------------------------ state marks
#
# docs/v3/BOSSMAN_DESIGN_SYSTEM.md, "The assistant column". The table is
# non-negotiable and closed: a mark outside it cannot be rendered, and no code
# path may collapse VERIFIED and REPORTED into one appearance.

MARK_VERIFIED = "verified"      # filled --accent check: post-state observed and matched
MARK_REPORTED = "reported"      # hollow --accent ring: the tool reported success only
MARK_WAITING = "waiting"        # --warn dot: waiting on the owner
MARK_FAILED = "failed"          # --danger cross: failed, typed reason on the row
MARK_PENDING = "pending"        # --text-faint ring: not started
MARKS = (MARK_VERIFIED, MARK_REPORTED, MARK_WAITING, MARK_FAILED, MARK_PENDING)

# Verification states of a run, as recorded by the finalizer. NOT_RUN is ours and
# means exactly "the finalizer has not observed anything for this run yet" — it
# is not a synonym for UNVERIFIED, which is a verdict the observer reached.
NOT_RUN = "NOT_RUN"
VERIFICATION_SOURCE = "finalize:verification.result"

# `tool_calls.status` — the closed set from bcc/db.py. An executor status outside
# this set is a fact we cannot interpret, and an uninterpretable status is never
# read as success.
STATUS_MARKS: dict[str, str] = {
    "pending_approval": MARK_WAITING,
    "approved": MARK_WAITING,
    "rejected": MARK_FAILED,
    "denied": MARK_FAILED,
    "error": MARK_FAILED,
    "timeout": MARK_FAILED,
    "executed": MARK_REPORTED,
}

_EXIT_CODE_RE = re.compile(r"exit_code=(-?\d+)\b")
# How many recent verification events to scan when locating a run's outcome.
_EVENT_SCAN = 400
# A target shorter than this cannot be linked to a call's arguments without
# matching by accident, so it is never linked at all.
_MIN_TARGET_LEN = 3
_DETAIL_MAX = 240


# ------------------------------------------------------------------ pure model

def mark_for(status: str, *, tool: str = "", error: str = "", result_preview: str = "",
             has_obligation: bool = False, obligation_verified: bool = False) -> tuple[str, str]:
    """(mark, typed reason) for one recorded tool call. Pure — no I/O, no model text.

    `obligation_verified` is the ONLY way into `MARK_VERIFIED`, and the caller
    may pass it True only after checking the finalizer's verdict and its
    freshness. Everything unclear resolves downward to the hollow ring.
    """
    if status in ("pending_approval", "approved"):
        return MARK_WAITING, "ожидает решения владельца"
    if status == "denied":
        return MARK_FAILED, "политика отклонила вызов: эффект не наступал"
    if status == "rejected":
        return MARK_FAILED, "владелец отклонил вызов: эффект не наступал"
    if status in ("error", "timeout"):
        return MARK_FAILED, redact_text(str(error or status))[:300]
    if status != "executed":
        # Not "not started" in the optimistic sense — simply not a status this
        # build knows how to read, and therefore not success.
        return MARK_PENDING, f"статус исполнителя не распознан: {status or '—'}"
    if error:
        return MARK_FAILED, redact_text(str(error))[:300]
    if tool == "terminal.run":
        match = _EXIT_CODE_RE.match(str(result_preview or ""))
        if match is None:
            return MARK_REPORTED, "код возврата команды не наблюдён"
        if int(match[1]) != 0:
            return MARK_FAILED, f"команда завершилась с кодом {match[1]}"
    if obligation_verified:
        return MARK_VERIFIED, "пост-состояние перечитано заново и совпало"
    if has_obligation:
        return MARK_REPORTED, ("исполнитель доложил об успехе; объявленное пост-состояние "
                               "ещё не подтверждено свежим наблюдением")
    return MARK_REPORTED, ("исполнитель доложил об успехе; объявленного пост-состояния у шага "
                           "нет — доказывать нечем")


def detail_of(tool: str, args: Any) -> str:
    """One readable line about what the call was given. Secrets are scrubbed by
    the same redactor the approval preview uses, never by a local guess."""
    safe = redact(args if isinstance(args, dict) else {})
    if tool == "terminal.run" and isinstance(safe, dict) and safe.get("command"):
        text = str(safe["command"])
    else:
        try:
            text = json.dumps(safe, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            text = str(safe)
    text = redact_text(text)
    return text[:_DETAIL_MAX] + ("…" if len(text) > _DETAIL_MAX else "")


def attach_obligations(call_args: list[Any], obligations: list[ExpectedState]) -> list[int | None]:
    """Which declared obligation (index) belongs to which call, or None.

    Deterministic textual link: the obligation's target must appear literally in
    the call's arguments. When several calls name the same target only the LAST
    one is linked, because the finalizer's single fresh observation covers the
    last effect, not the earlier ones. Everything unlinked stays unlinked.
    """
    linked: list[int | None] = [None] * len(call_args)
    blobs: list[str] = []
    for args in call_args:
        try:
            blobs.append(json.dumps(args if isinstance(args, dict) else {}, ensure_ascii=False,
                                    sort_keys=True, default=str))
        except (TypeError, ValueError):
            blobs.append(str(args))
    for oi, exp in enumerate(obligations):
        target = str(exp.target or "")
        if len(target) < _MIN_TARGET_LEN:
            continue
        for ci in range(len(blobs) - 1, -1, -1):
            if linked[ci] is None and target in blobs[ci]:
                linked[ci] = oi
                break
    return linked


@dataclass
class Step:
    index: int
    call_id: str
    step: int
    capability: str
    title: str
    detail: str
    executor: str
    executor_known: bool
    effect_class: str
    side_effect: bool
    verification_strategy: str
    provable: bool
    status: str
    mark: str
    verified: bool
    reason: str
    elapsed_ms: int | None
    started_at: str | None
    finished_at: str | None
    obligation: dict | None = None
    approval: dict | None = None
    grant: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _ms(start: Any, end: Any) -> int | None:
    if isinstance(start, datetime) and isinstance(end, datetime):
        return int((end - start).total_seconds() * 1000)
    return None


# ------------------------------------------------------------------ data access

async def _latest_run(s, task_id: int, run_id: int | None) -> dict | None:
    if run_id is not None:
        row = await fetch_one(s, runs_t, run_id)
        return row if row and row["task_id"] == task_id else None
    res = await s.execute(sa.select(runs_t).where(runs_t.c.task_id == task_id)
                          .order_by(runs_t.c.id.desc()).limit(1))
    row = res.first()
    return dict(row._mapping) if row else None


async def run_verification(svc, run_id: int) -> dict[str, Any]:
    """The finalizer's own verification verdict for this run, or NOT_RUN.

    Read from the `verification.result` event `bcc/finalize.py` emits — not
    recomputed here. Re-observing the world from the panel would be a second
    verifier that could disagree with the one that actually gates completion.
    """
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(events_t)
                                .where(events_t.c.kind == "verification.result")
                                .order_by(events_t.c.id.desc()).limit(_EVENT_SCAN))).fetchall()
    for row in rows:
        m = dict(row._mapping)
        data = m.get("data") if isinstance(m.get("data"), dict) else {}
        try:
            same = int(data.get("run_id")) == int(run_id)
        except (TypeError, ValueError):
            same = False
        if same:
            return {"status": str(data.get("status") or "UNVERIFIED"),
                    "reason": str(data.get("reason") or "")[:300],
                    "at": _iso(m.get("ts")), "at_dt": m.get("ts"), "source": VERIFICATION_SOURCE}
    return {"status": NOT_RUN, "at": None, "at_dt": None, "source": VERIFICATION_SOURCE,
            "reason": "финализатор ещё не наблюдал пост-состояние этого run'а"}


async def _finalized(svc, run_id: int) -> dict | None:
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(events_t)
                                .where(events_t.c.kind == "task.finalized")
                                .order_by(events_t.c.id.desc()).limit(_EVENT_SCAN))).fetchall()
    for row in rows:
        m = dict(row._mapping)
        data = m.get("data") if isinstance(m.get("data"), dict) else {}
        try:
            same = int(data.get("run_id")) == int(run_id)
        except (TypeError, ValueError):
            same = False
        if same:
            return {"at": _iso(m.get("ts")), "override": bool(data.get("override"))}
    return None


def _declared(task: dict) -> tuple[list[ExpectedState], bool, str]:
    """Declared obligations of the task, parsed by the FINALIZER's parser.

    The second value says whether the contract is well formed. `finalize` refuses
    to finalize a task whose declared effects do not all parse; the panel shows
    that as a broken contract instead of silently listing the survivors.
    """
    meta = task.get("meta") if isinstance(task.get("meta"), dict) else {}
    raw = list(((meta.get("review") or {}).get("evidence")) or []) + \
        list(meta.get("required_effects") or [])
    parsed = parse_expected(raw)
    if len(parsed) != len(raw):
        return parsed, False, ("контракт объявленных эффектов не разбирается целиком: "
                               f"{len(raw)} объявлено, {len(parsed)} читаемо — финализатор "
                               "откажет в завершении, пока это не исправлено")
    return parsed, True, ""


async def build_plan(svc, task_id: int, *, run_id: int | None = None,
                     probes: dict[str, bool] | None = None) -> dict[str, Any]:
    """The plan of one task: its steps, their capabilities, obligations and marks.

    Every field is derived from a row the engine or the finalizer already wrote.
    The model's answer text is not read at all, at any point.
    """
    async with svc.db.session() as s:
        task = await fetch_one(s, tasks_t, task_id)
        if task is None:
            raise HTTPException(404, {"message": f"задача не найдена: {task_id}"})
        run = await _latest_run(s, task_id, run_id)
        runs = [dict(r._mapping) for r in (await s.execute(
            sa.select(runs_t.c.id, runs_t.c.attempt, runs_t.c.status, runs_t.c.started_at,
                      runs_t.c.finished_at)
            .where(runs_t.c.task_id == task_id).order_by(runs_t.c.id))).fetchall()]
        calls: list[dict] = []
        approvals: dict[int, dict] = {}
        if run is not None:
            calls = [dict(r._mapping) for r in (await s.execute(
                sa.select(tool_calls_t).where(tool_calls_t.c.run_id == run["id"])
                .order_by(tool_calls_t.c.id))).fetchall()]
            ids = [c["approval_id"] for c in calls if c.get("approval_id")]
            if ids:
                approvals = {int(dict(r._mapping)["id"]): dict(r._mapping) for r in (await s.execute(
                    sa.select(approvals_t).where(approvals_t.c.id.in_(ids)))).fetchall()}

    obligations, contract_ok, contract_note = _declared(task)
    verification = ({"status": NOT_RUN, "at": None, "at_dt": None, "source": VERIFICATION_SOURCE,
                     "reason": "у задачи ещё нет ни одного run'а"}
                    if run is None else await run_verification(svc, int(run["id"])))
    run_verified = verification["status"] == "VERIFIED"
    linked = attach_obligations([c.get("args") for c in calls], obligations)

    notes: list[str] = []
    if not contract_ok:
        notes.append(contract_note)

    steps: list[Step] = []
    obligation_state: list[dict] = [
        {"index": i, "kind": e.kind, "target": e.target, "expect": dict(e.expect),
         "verified": False, "step_index": None} for i, e in enumerate(obligations)]

    for index, call in enumerate(calls):
        tool = str(call.get("tool") or "")
        cap, gr = cap_mod.for_tool(REGISTRY, tool, args=call.get("args") or {}, probes=probes)
        oi = linked[index]
        obligation = obligations[oi] if oi is not None else None

        # Freshness, the same rule finalize_task applies: an observation made
        # before this step finished cannot be evidence about this step.
        fresh = True
        if isinstance(call.get("finished_at"), datetime) and verification.get("at_dt") is not None:
            fresh = verification["at_dt"] >= call["finished_at"]
        obligation_verified = bool(obligation is not None and run_verified and fresh)
        if obligation is not None and run_verified and not fresh:
            notes.append(f"шаг {index + 1}: наблюдение старше самого шага — доказательством "
                         f"для него не считается")

        mark, reason = mark_for(str(call.get("status") or ""), tool=tool,
                                error=str(call.get("error") or ""),
                                result_preview=str(call.get("result_preview") or ""),
                                has_obligation=obligation is not None,
                                obligation_verified=obligation_verified)
        approval = approvals.get(int(call["approval_id"])) if call.get("approval_id") else None
        steps.append(Step(
            index=index, call_id=str(call.get("call_id") or ""), step=int(call.get("step") or 0),
            capability=tool, title=tool, detail=detail_of(tool, call.get("args")),
            executor=(cap.source if cap is not None else ""), executor_known=cap is not None,
            effect_class=(cap.effect_class if cap is not None else ""),
            side_effect=bool(cap is not None and cap.side_effect),
            verification_strategy=(cap.verification_strategy if cap is not None else ""),
            provable=bool(cap is not None and cap.provable),
            status=str(call.get("status") or ""), mark=mark,
            verified=mark == MARK_VERIFIED, reason=reason,
            elapsed_ms=(int(call["duration_ms"]) if call.get("duration_ms") is not None
                        else _ms(call.get("created_at"), call.get("finished_at"))),
            started_at=_iso(call.get("created_at")), finished_at=_iso(call.get("finished_at")),
            obligation=({"index": oi, "kind": obligation.kind, "target": obligation.target,
                         "expect": dict(obligation.expect)} if obligation is not None else None),
            approval=({"id": int(approval["id"]), "kind": str(approval["kind"]),
                       "status": str(approval["status"])} if approval else None),
            grant=gr.to_dict()))
        if oi is not None:
            obligation_state[oi]["step_index"] = index
            obligation_state[oi]["verified"] = obligation_verified

    # An obligation nobody's arguments named is still owed. It is verified only
    # when the finalizer said VERIFIED for the run that carries it.
    for state in obligation_state:
        if state["step_index"] is None:
            state["verified"] = bool(run_verified)

    counts = {mark: sum(1 for st in steps if st.mark == mark) for mark in MARKS}
    if run is not None and not calls:
        notes.append("в этом run'е не записано ни одного вызова инструмента: "
                     "текст ответа шагом плана не является")
    if obligations and verification["status"] == NOT_RUN:
        notes.append("объявленные эффекты есть, наблюдение ещё не проводилось — "
                     "ни один шаг не может быть показан подтверждённым")
    if not obligations and any(st.side_effect for st in steps):
        notes.append("у задачи нет объявленного пост-состояния: эффекты её шагов "
                     "доказать нечем, они показаны как «доложено»")

    body: dict[str, Any] = {
        "task_id": int(task["id"]),
        "run_id": int(run["id"]) if run is not None else None,
        "request": {"title": str(task.get("title") or ""),
                    "prompt": redact_text(str(task.get("prompt") or ""))[:2000],
                    "status": str(task.get("status") or ""),
                    "kind": str(task.get("kind") or ""),
                    "created_at": _iso(task.get("created_at"))},
        "steps": [st.to_dict() for st in steps],
        "obligations": obligation_state,
        "obligations_valid": contract_ok,
        "verification": {k: v for k, v in verification.items() if k != "at_dt"},
        "finalized": (await _finalized(svc, int(run["id"]))) if run is not None else None,
        "counts": counts,
        "elapsed_ms": _ms(run.get("started_at"), run.get("finished_at")) if run else None,
        "runs": [{"id": int(r["id"]), "attempt": int(r["attempt"] or 0),
                  "status": str(r["status"] or ""), "started_at": _iso(r.get("started_at")),
                  "finished_at": _iso(r.get("finished_at"))} for r in runs],
        "notes": notes,
        "marks": list(MARKS),
    }
    body["version"] = plan_version(body)
    return body


def plan_version(body: dict[str, Any]) -> str:
    """Digest of everything the panel would redraw. Lets a poll answer
    "nothing changed" in one short response instead of the whole plan."""
    core = {
        "task": body["request"]["status"], "run": body.get("run_id"),
        "verification": body["verification"].get("status"),
        "finalized": bool(body.get("finalized")),
        "steps": [[st["capability"], st["status"], st["mark"], st["elapsed_ms"], st["reason"]]
                  for st in body["steps"]],
        "obligations": [[o["kind"], o["target"], o["verified"]] for o in body["obligations"]],
        "notes": body["notes"],
    }
    return payload_digest(core)


# ------------------------------------------------------------ capability surface

# Capabilities this build DECLARES it understands but for which no executor the
# model can call exists at all. Derived from features.action_contract.CAPABILITIES
# (its `tool_sources` is the executor family) so the two can never drift: adding
# an executor there removes the block here without touching this file.
def declared_without_executor() -> list[dict[str, Any]]:
    from .action_contract import CAPABILITIES, _family_tool_names
    out: list[dict[str, Any]] = []
    for cap in CAPABILITIES:
        if _family_tool_names(cap.tool_sources):
            continue
        gr = cap_mod.blocked(cap.name, reason_key=cap_mod.NO_EXECUTOR)
        out.append({"capability_id": cap.name, "tool": "", "executor": "",
                    "description": "распознаётся в задаче, но исполнителя в сборке нет",
                    "effect_class": "", "verification_strategy": "", "provable": False,
                    "side_effect": True, "idempotency": "", "approval_requirement": "deny",
                    "permission": "", "privacy_requirement": "", "source": "declared",
                    "generation": 0, "supported_platforms": [], "grant": gr.to_dict()})
    return out


async def capability_surface(svc, probes: dict[str, bool] | None = None) -> dict[str, Any]:
    """What this build can do, who serves each capability, and why the rest cannot.

    Registered capabilities come from the live registry through
    `bcc.capability.surface`; the declared-but-unserved ones come from
    action_contract. Both carry the remediation table's answer, because a refusal
    with no way out is a dead end, not safety.
    """
    items = cap_mod.surface(REGISTRY, probes=probes)
    unserved = declared_without_executor()
    everything = items + unserved
    return {
        "capabilities": everything,
        "granted": sum(1 for c in everything if c["grant"]["granted"]),
        "blocked": sum(1 for c in everything if not c["grant"]["granted"]),
        "probes": dict(probes or {}),
        "provable_kinds": list(KINDS),
        "executors": sorted({str(c["executor"]) for c in everything if c["executor"]}),
    }


# --------------------------------------------------------------- quick actions

@dataclass(frozen=True)
class QuickAction:
    """A declarative quick action: a capability plus the post-state it owes.

    Every field here is a declaration, not an execution path. Submitting one
    creates an ordinary task whose `meta.required_effects` carries the expected
    post-state, so `finalize` verifies it exactly as it verifies anything else.
    There is no shortcut from this module to a completed task.
    """
    action_id: str
    label: str
    icon: str
    capability: str                 # tool name (== capability_id) that must serve it
    prompt: str                     # {target} is substituted with the owner's value
    expect_kind: str                # a kind from bcc.v2.verification.KINDS
    expect: dict[str, Any]          # literal expectation; "{target}" is substituted
    target_label: str               # what the owner must supply
    absent: str = cap_mod.UNREGISTERED

    def expected_state(self, target: str) -> dict[str, Any]:
        expect = {k: (target if v == "{target}" else v) for k, v in self.expect.items()}
        return {"kind": self.expect_kind, "target": target, "expect": expect}

    def to_dict(self) -> dict[str, Any]:
        return {"action_id": self.action_id, "label": self.label, "icon": self.icon,
                "capability": self.capability, "expect_kind": self.expect_kind,
                "expect": dict(self.expect), "target_label": self.target_label,
                "prompt": self.prompt}


QUICK_ACTIONS: tuple[QuickAction, ...] = (
    QuickAction("file.create", "Создать файл", "file", "terminal.run",
                "Создай файл {target} и запиши в него результат работы.",
                "file", {"exists": True}, "путь к файлу"),
    QuickAction("fact.remember", "Запомнить факт", "memory", "memory.fact.add",
                "Запомни факт о «{target}» в долговременной памяти.",
                "memory", {"current": True}, "тема факта"),
    QuickAction("app.start", "Запустить приложение", "app", "apps.start",
                "Запусти приложение {target}.",
                "app", {"running": True}, "идентификатор приложения"),
    QuickAction("page.open", "Открыть страницу", "browser", "browser.open",
                "Открой страницу {target} в браузере.",
                "browser", {"url_contains": "{target}"}, "адрес страницы"),
    # Deliberately present and deliberately unavailable: image generation has no
    # tool the model can call in this build (see action_contract IMAGES_ACTION).
    # The honest block with its remediation is the answer; a pretend execution
    # would be the alternative, and that is the thing this repository refuses.
    QuickAction("image.generate", "Сгенерировать изображение", "image", "images.generate",
                "Сгенерируй изображение: {target}.",
                "file", {"exists": True}, "описание изображения",
                absent=cap_mod.NO_EXECUTOR),
)
BY_ID = {a.action_id: a for a in QUICK_ACTIONS}


async def quick_action_catalog(svc, probes: dict[str, bool] | None = None) -> dict[str, Any]:
    items = []
    for action in QUICK_ACTIONS:
        cap, gr = cap_mod.for_tool(REGISTRY, action.capability, absent=action.absent,
                                   probes=probes)
        items.append({**action.to_dict(),
                      "executor": cap.source if cap is not None else "",
                      "verification_strategy": cap.verification_strategy if cap else "",
                      "available": gr.granted, "grant": gr.to_dict()})
    return {"actions": items, "probes": dict(probes or {})}


# --------------------------------------------------------------------- endpoints

async def _probes(svc) -> dict[str, bool]:
    from ..api import _capability_probes
    return await _capability_probes(svc)


@router.get("/assistant/plan/{task_id}")
async def assistant_plan(task_id: int, request: Request, run_id: int | None = None,
                         version: str = ""):
    """The execution plan of a task.

    `?version=<digest>` makes this a compact poll: when nothing the panel draws
    has changed, the answer is three fields instead of the whole plan.
    """
    svc = request.app.state.svc
    body = await build_plan(svc, task_id, probes=await _probes(svc), run_id=run_id)
    if version and version == body["version"]:
        return {"task_id": body["task_id"], "version": body["version"], "changed": False}
    return {**body, "changed": True}


@router.get("/assistant/capabilities")
async def assistant_capabilities(request: Request):
    """What this build can do, and for anything it cannot — why, plus the way out."""
    svc = request.app.state.svc
    return await capability_surface(svc, probes=await _probes(svc))


@router.get("/assistant/quick-actions")
async def assistant_quick_actions(request: Request):
    svc = request.app.state.svc
    return await quick_action_catalog(svc, probes=await _probes(svc))


class QuickActionIn(BaseModel):
    target: str = Field(default="", max_length=500)
    agent_id: int | None = None
    run_now: bool = False


@router.post("/assistant/quick-actions/{action_id}")
async def assistant_run_quick_action(action_id: str, body: QuickActionIn, request: Request):
    """Submit a quick action as an ordinary task carrying its declared post-state.

    A refused capability produces 409 with the reason and the remediation, and NO
    task: an action nothing can execute must not become a task that will later be
    reported as done.
    """
    action = BY_ID.get(action_id)
    if action is None:
        raise HTTPException(404, {"message": f"быстрое действие не объявлено: {action_id}",
                                  "known": sorted(BY_ID)})
    target = body.target.strip()
    if not target:
        raise HTTPException(400, {"message": f"нужно значение: {action.target_label}"})
    svc = request.app.state.svc
    _cap, gr = cap_mod.for_tool(REGISTRY, action.capability, absent=action.absent,
                                probes=await _probes(svc))
    if not gr.granted:
        raise HTTPException(409, {"message": gr.reason or "способность недоступна",
                                  "capability": action.capability,
                                  "grant": gr.to_dict(),
                                  "remediation": gr.remediation})

    expected = action.expected_state(target)
    prompt = action.prompt.replace("{target}", target)
    meta = {"allowed_tools": [action.capability], "required_effects": [expected],
            "assistant_quick_action": action.action_id}
    async with svc.db.session() as s:
        res = await s.execute(sa.insert(tasks_t).values(
            title=action.label, prompt=prompt, agent_id=body.agent_id, priority=5,
            max_retries=2, status="draft", meta=meta, created_at=utcnow(), updated_at=utcnow()))
        task_id = int(res.inserted_primary_key[0])
        await s.commit()
    await svc.bus.emit("task.created", task_id=task_id, title=action.label,
                       agent_id=body.agent_id, quick_action=action.action_id)
    if body.run_now:
        await svc.engine.enqueue(task_id)
    return {"task_id": task_id, "capability": action.capability, "expected": expected,
            "grant": gr.to_dict(), "queued": bool(body.run_now)}


FEATURE = Feature(name="assistant_plan", router=router)
