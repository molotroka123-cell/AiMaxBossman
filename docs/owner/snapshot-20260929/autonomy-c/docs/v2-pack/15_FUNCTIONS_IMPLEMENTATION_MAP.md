# BOSSMAN V2 — 15 Function Implementation Map

| # | Function | Current foundation | V2 owner | Prewritten code in this pack |
|---|---|---|---|---|
| 01 | Autopilot Objective | tasks + scheduler + persistence | Agent 01 | schema/logic guided by master prompt |
| 02 | Smart Model Router | provider/model registry + fallback | Agent 02 | `model_router.py`, OpenRouter metadata |
| 03 | AI Governor | events + retries + task status | Agent 03 | `governor.py` |
| 04 | Model Benchmark Lab | current mini benchmark | Agent 04 | OpenRouter capability probes + role router inputs |
| 05 | Replay/Fork | checkpoint exists | Agent 05 | `replay.py`, OpenCode fork bridge |
| 06 | Visual Agent Map | events exist | Agent 06 | `agent_graph.py` |
| 07 | Worktree + Terminal + OpenCode | sandbox shell/git already in bossman-core | Agent 07 | `terminal_control.py`, `opencode_bridge.py` |
| 08 | Reviewer Gate | tasks/retries exist | Agent 08 | `reviewer_gate.py` |
| 09 | Browser Live Control | approvals/event foundation | Agent 09 | `browser_control.py` |
| 10 | Skills + Skill Forge + MCP Hub | ToolDef runtime exists in bossman-core | Agent 10 | `skill_library.py`, `mcp_hub.py`, first skills |
| 11 | Natural Language Orchestration | agents/tasks config exists | Agent 11 | `orchestration_schema.py` |
| 12 | Resource Brain | metrics + llama-swap concept | Agent 12 | `resource_brain.py` |
| 13 | Mission KPI | event/task data exists | Agent 13 | `kpi.py` |
| 14 | Self-Healing | queue crash recovery exists | Agent 14 | `recovery.py` |
| 15 | Mobile Command Mode | PWA/mobile base exists | Agent 15 | UX reference + acceptance prompt |

## Critical prerequisite before parallel schema changes

The current Command Center schema still relies on SQLAlchemy `metadata.create_all()`.

Before 15 agents concurrently introduce entities, move to a centralized migration flow
(Alembic is the preferred default) or designate one Integration Lead as the only migration owner.

## Canonical runtime direction

Do not keep two permanent agent runtimes.

The target should combine:

- Command Center persistent queue/scheduler/recovery
- bossman-core tool-call/permission/context runtime

into one canonical Task/Run/Agent/Approval/Event path.

## Worker pool

The final orchestration cannot remain effectively one LLM job at a time.

Introduce bounded concurrency controlled by Resource Brain.

## Hard cancellation

Current stop semantics should evolve so active HTTP/model/browser/terminal operations can be cancelled,
not only noticed between steps.
