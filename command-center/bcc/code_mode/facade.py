"""Code-mode facade: many tools behind two functions (opt-in).

Instead of putting every granted tool schema into every model call, the model
sees TWO tools:

  bossman_search(query, k)  -> Python signatures of the few relevant tools;
  bossman_run(code)         -> runs a short snippet whose `tools.<name>(...)`
                               calls go through the engine's normal per-call
                               pipeline (see `engine_bridge`).

The facade only ever NARROWS: the callable set is the set the agent/task is
granted today (`allowed_tools_for`), minus what the policy denies outright and
what is owner-only for a guest-bound agent. Every call inside the sandbox is a
separate engine tool call with its own approval, journal row and STOP check.
"""
from __future__ import annotations

import os
from typing import Any

from ..tools import (ToolContext, ToolResult, ToolSpec, agent_policy_rules, allowed_tools_for,
                     context_denial, decide_effect, REGISTRY)
from .catalog import ToolCatalog

FACADE_SEARCH = "bossman.search"
FACADE_RUN = "bossman.run"
FACADE_NAMES = frozenset({FACADE_SEARCH, FACADE_RUN})
FLAG = "code_mode"

SEARCH_DESCRIPTION = (
    "Find tools by what you want to do (a few words). Returns the Python signatures of the best "
    "matches; call them from bossman_run. Use it before bossman_run.")
RUN_DESCRIPTION = (
    "Run a short Python snippet that uses tools: r = tools.<name>(arg=value) (keyword args only; "
    "each call returns {ok, content}). Find <name> with bossman_search. print() what you need; the "
    "last expression is returned. No imports, files or network. A tool that needs owner approval "
    "stops the snippet and is asked as its own step.")


def code_mode_enabled(task: dict | None, agent: dict | None) -> bool:
    """Opt-in. Order: task.meta.code_mode (explicit true/false) -> agent.permissions.code_mode ->
    env BOSSMAN_CODE_MODE. Default OFF. The model cannot set any of these."""
    meta = (task or {}).get("meta")
    if isinstance(meta, dict) and FLAG in meta:
        return meta.get(FLAG) is True
    perms = (agent or {}).get("permissions")
    if isinstance(perms, dict) and FLAG in perms:
        return perms.get(FLAG) is True
    return os.environ.get("BOSSMAN_CODE_MODE", "").strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------- visibility

async def _is_guest_agent(svc: Any, agent: dict) -> bool:
    """Agent bound to a non-owner Telegram person. Unreadable config -> treated as guest (fail closed)."""
    agent_id = (agent or {}).get("id")
    if svc is None or not isinstance(agent_id, int):
        return False
    try:
        from ..gmail_connector import _participant_agent_ids
        return agent_id in await _participant_agent_ids(svc)
    except Exception:  # noqa: BLE001
        return True


async def visible_specs(ctx: ToolContext, specs: list[ToolSpec], policy_rules: list[dict] | None = None
                        ) -> tuple[list[ToolSpec], dict[str, str]]:
    """(specs the facade may show/call, {name: effect-hint}).

    Never widens: `specs` already is the granted set. Drops tools the policy denies for any
    arguments, owner-only tools (gmail) for guest-bound agents, and for guests everything outside
    the Telegram participant perimeter."""
    from ..gmail_connector import owner_only_denial
    from ..pit.policy import TelegramToolPolicy

    rules = policy_rules if policy_rules is not None else agent_policy_rules(ctx.agent)
    guest = await _is_guest_agent(ctx.svc, ctx.agent)
    perimeter = TelegramToolPolicy()
    out: list[ToolSpec] = []
    effects: dict[str, str] = {}
    for spec in specs:
        if spec.name in FACADE_NAMES:
            continue
        if guest and (spec.context_deny is owner_only_denial or not perimeter.allows(spec.name)):
            continue
        effect, _ = decide_effect(spec, {}, ctx.agent, rules)
        if effect == "deny":
            continue
        if spec.context_deny is owner_only_denial and await context_denial(spec, {}, ctx):
            continue
        out.append(spec)
        effects[spec.name] = effect
    return out, effects


async def build_catalog(ctx: ToolContext, specs: list[ToolSpec], policy_rules: list[dict] | None = None
                        ) -> ToolCatalog:
    shown, effects = await visible_specs(ctx, specs, policy_rules)
    return ToolCatalog(shown, effects=effects)


# ------------------------------------------------------------------ the tools

async def _search_handler(args: dict, ctx: ToolContext) -> ToolResult:
    query = str(args.get("query") or "").strip()
    try:
        k = int(args.get("k") or 5)
    except (TypeError, ValueError):
        k = 5
    granted = [s for s in REGISTRY.resolve(allowed_tools_for(ctx.task, ctx.agent))]
    catalog = await build_catalog(ctx, granted)
    text = catalog.render_search(query, k)
    return ToolResult(content=text, one_line=f"bossman.search: {query[:60]}")


async def _run_placeholder(args: dict, ctx: ToolContext) -> ToolResult:
    # bossman.run is executed by the engine (engine_bridge.run_code_step), which owns the
    # approval/journal/STOP machinery. Reaching this handler means something called the tool
    # outside the engine: refuse rather than run code without the per-call pipeline.
    return ToolResult(content="bossman.run выполняется только движком задач", error=True,
                      one_line="bossman.run: вне движка")


_SPECS: tuple[ToolSpec, ToolSpec] | None = None


def facade_specs() -> tuple[ToolSpec, ToolSpec]:
    global _SPECS
    if _SPECS is None:
        search = ToolSpec(
            name=FACADE_SEARCH, description=SEARCH_DESCRIPTION, handler=_search_handler,
            input_schema={"query": {"type": "string", "description": "what you want to do"},
                          "k": {"type": "integer", "description": "how many tools, 1-10 (default 5)"}},
            required=["query"], category="read", permission="", source="code_mode",
            default_effect="auto", timeout_seconds=20.0, idempotent=True)
        run = ToolSpec(
            name=FACADE_RUN, description=RUN_DESCRIPTION, handler=_run_placeholder,
            input_schema={"code": {"type": "string", "description": "Python snippet"}},
            required=["code"], category="exec", permission="", source="code_mode",
            default_effect="auto", timeout_seconds=600.0, idempotent=True)
        search.generation = 1
        run.generation = 1
        _SPECS = (search, run)
    return _SPECS


# ------------------------------------------------------------------ rendering

def render_run_result(outcome: Any, calls: list[dict], ask: dict | None = None) -> str:
    """Compact text for the model. Intermediate tool results never appear here - only what the
    snippet printed/returned."""
    lines = [f"status: {outcome.status}"]
    if calls:
        lines.append("calls: " + "; ".join(
            f"{c['n']}. {c['tool']} -> {c['status']}" for c in calls))
    if outcome.output:
        lines.append("output:\n" + outcome.output.rstrip("\n"))
        if outcome.truncated:
            lines.append("[output truncated]")
    if outcome.value is not None:
        lines.append(f"value: {outcome.value!r}")
    if outcome.error:
        lines.append(f"error: {outcome.error}")
    if outcome.status == "timeout":
        lines.append("the snippet was killed; make it shorter or split the work")
    if outcome.status == "aborted" and outcome.abort_kind == "stop":
        lines.append("stopped by the owner")
    if ask:
        lines.append(
            f"needs_approval: {ask['tool']} {ask['args_preview']}\n"
            "This exact call was submitted to the owner as a separate step; the rest of the snippet "
            "did NOT run. You will get that call's result next; then continue in a new snippet. "
            "Do not repeat the call.")
    return "\n".join(lines)
