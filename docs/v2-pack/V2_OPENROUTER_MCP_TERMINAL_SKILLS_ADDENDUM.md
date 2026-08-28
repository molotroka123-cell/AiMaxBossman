# BOSSMAN V2 — OpenRouter + MCP + Skills + Terminal Addendum

This addendum is meant to be appended to the existing 15-agent V2 master plan.

## Current repository reality

### OpenRouter

The repository is already partially compatible:
- `bossman-infra/litellm/config.yaml` defines an OpenRouter cloud fallback.
- `command-center/bcc/providers.py` calls any OpenAI-compatible `/models` and `/chat/completions` endpoint.

But V2 is not complete for OpenRouter:
- the generic model listing keeps only model IDs
- OpenRouter pricing/context/modalities/capabilities are not synchronized into the model registry
- Command Center's OpenAI-compatible adapter does not yet forward tool schemas/tool_choice/structured-output controls
- dynamic OpenRouter catalog refresh is missing
- advertised capabilities are not verified per model

Implement a first-class `OpenRouterProviderAdapter` or provider-specialization layer.

### MCP

No native MCP manager/client was found in the current repository.

`bossman-core` already has its own ToolDef registry and permission/approval execution loop.
Reuse that as the canonical BOSSMAN tool security model and add MCP as an external tool source.

### Skills

No native Skill Library/loader was found in the current repo.

OpenCode-compatible skills should use `.agents/skills/<id>/SKILL.md` as BOSSMAN's preferred portable source.
Also support import/discovery from OpenCode and Claude-compatible directories.

### Terminal / whole computer

`bossman-core/bossman/toolkit/shell.py` already runs `run` and `tests`, but in Docker mode it uses:
- `--network none`
- only the agent workdir mounted

`bossman-core/bossman/toolkit/files.py` explicitly rejects paths outside the workdir.

Therefore current BOSSMAN does NOT have unrestricted whole-computer access, which is good.
V2 should add explicit scoped host access rather than removing those protections.

---

# Changes to the 15 agents

## Agent 02 — Smart Model Router

Add:
- OpenRouter catalog metadata as routing input
- verified capability flags
- real historical success by task type
- price/context/latency/throughput
- privacy/ZDR/provider preference when available
- distinction between BOSSMAN routing and OpenRouter internal routing

Never allow OpenRouter Auto Router to hide routing decisions when a mission requires a fixed model/reviewer.

## Agent 04 — Model Benchmark Lab

Add provider compatibility certification:
- chat
- stream
- tools
- structured output
- image input
- context probe

Store both advertised and verified capabilities.

## Agent 07 — expand to Workspace / Terminal / Worktree Runtime

This agent now owns:
- worktrees
- sandbox shell
- PTY/live terminal sessions
- command logs
- process kill
- scoped host roots
- OpenCode execution adapter

### Terminal modes

1. `sandbox` — default
2. `project_host` — approval-gated
3. `system_admin` — explicit privileged mode

### Permission actions

- terminal.read
- terminal.run
- terminal.kill
- terminal.host
- terminal.admin
- filesystem.external.read
- filesystem.external.write

### UI

Add `Terminal` page:
- active sessions
- agent/task
- cwd
- process
- live output
- exact commands
- Stop/Kill
- Take Over
- permission policy

Never provide a generic "whole computer = allow" switch.

## Agent 09 — Browser Live View

Keep Playwright DOM-first architecture.
Integrate the previously prepared browser-control handoff.

## Agent 10 — expand Skill Library to Skill + MCP Hub

This agent now owns three related modules.

### A. Skill Library

Discover sources:
- `.agents/skills`
- `.opencode/skills`
- `.claude/skills`
- `~/.agents/skills`
- `~/.config/opencode/skills`
- `~/.claude/skills`
- explicitly configured directories

Never recursively scan the entire computer for skills.

Support:
- import
- source tracking
- versioning
- diff
- enable/disable
- agent permissions
- export
- validation

### B. Skill Forge

Use `.agents/skills/skill-forge/SKILL.md`.

After a task/conversation, it may propose:
- create new skill
- improve existing skill

Skill changes that broaden permissions require approval.

### C. MCP Manager

Tables/entities:
- mcp_servers
- mcp_tools
- mcp_resources
- mcp_prompts
- mcp_health

Support local stdio MCP and remote HTTP transports as appropriate.

Expose discovered MCP tools into canonical Tool Registry as namespaced tools:

`mcp:<server-id>:<tool-name>`

Per-agent permission:
- AUTO / ASK / DENY

Do not place every MCP tool into every agent context.
Advertise only assigned tools to reduce tokens.

## Agent 11 — Natural Language Orchestration

Extend natural language compiler to permissions:

Example:

"Coder may edit D:/Projects, run tests automatically, ask before npm install and git push, never access wallets."

Compile into structured policies and show preview before application.

## Agent 12 — Resource Brain

Track:
- model memory
- browser processes
- terminal jobs
- OpenCode worker processes
- MCP server processes

Resource Brain can pause/queue heavy jobs but cannot silently grant permissions.

## Agent 14 — Self-Healing

Add health/recovery for:
- MCP server process
- OpenCode adapter
- terminal PTY worker
- Playwright

No automatic replay of non-idempotent external submits.

## Agent 15 — Mobile Command Mode

Mobile quick controls:
- terminal Stop/Kill
- browser Take Over/Pause
- MCP failure alert
- skill update approval
- permission approval

---

# OpenRouter implementation requirements

## Provider setup

User enters API key once.
Store encrypted.
Default base URL:

`https://openrouter.ai/api/v1`

## Catalog sync

Call `/models`.
Persist remote catalog separately from pinned/active BOSSMAN models.

Map at least:
- id
- name
- context length
- pricing
- input/output modalities
- architecture/tokenizer when relevant
- provider details link
- supported parameters when returned
- last_synced_at

## Filters in UI

- search
- coding
- tools
- vision
- context
- price
- verified only
- pinned
- active
- stale

## Sync semantics

- never delete run history
- removed remote models -> stale
- user aliases survive refresh
- role assignments survive refresh
- model updates should be diffed

## Live verification

When pinning a model, optionally run:
1. chat probe
2. tool-call probe
3. structured-output probe
4. vision probe if advertised
5. stream probe

Use small cheap prompts.

---

# OpenCode Bridge

Do not copy all of OpenCode.

Use it as an optional execution engine for repository coding.

BOSSMAN stays canonical for:
- mission
- task
- budget
- approval
- agent/team
- model policy
- resource scheduling
- history

OpenCode adapter owns:
- coding session
- shell/edit tooling
- subagent execution if configured
- OpenCode skill consumption

BOSSMAN should be able to:
- create OpenCode session
- attach project/worktree
- select model/agent
- send task
- stream events
- abort
- collect diff/artifacts
- persist session ID
- resume/fork if supported

Do not grant OpenCode unrestricted external-directory access by default.

---

# Recommended access matrix

| Role | Project files | External files | Terminal | Network | Browser | Git push | System admin |
|---|---|---|---|---|---|---|---|
| Manager | read | deny | deny | limited | read | deny | deny |
| Coder | rw worktree | deny by default | sandbox auto | package/net ask | optional | ASK | deny |
| Research | read artifacts | deny | deny | web allowed | DOM/click auto | deny | deny |
| Browser agent | session only | downloads ASK | deny | web allowed | policy-based | deny | deny |
| Reviewer | read | deny | tests auto | deny | UI QA | deny | deny |
| Skill Forge | skill roots rw | configured skill roots only | deny | docs optional | deny | deny | deny |
| MCP Manager | config rw | explicit server dirs | server start/stop ASK | per MCP | deny | deny | deny |
| Resource Brain | metrics | deny | no arbitrary shell | deny | deny | deny | model/process controls only |
| Governor | runtime metadata | deny | stop/pause only | deny | stop/pause | deny | deny |

Secrets:
- never model-visible by default
- inject at tool boundary
- `.env`, SSH keys, wallet stores, browser password DB = deny unless narrowly designed connector needs them

---

# First Skill Pack

Install the accompanying directories under:

`.agents/skills/`

Initial skills:
- repo-audit
- safe-code-change
- browser-research
- safe-terminal
- model-eval
- openrouter-sync
- night-mission
- skill-forge

BOSSMAN and OpenCode should both discover these.

---

# Acceptance criteria

The work is complete only when:

1. OpenRouter key can be added in UI.
2. OpenRouter catalog can be synchronized.
3. pricing/context/modalities are shown.
4. a pinned OpenRouter model can pass a live chat probe.
5. tool calling is actually verified for at least one supporting OpenRouter model.
6. MCP server can be added and health-checked.
7. MCP tools appear only for assigned agents.
8. an MCP tool invocation respects AUTO/ASK/DENY.
9. OpenCode skill directories are discovered/imported.
10. a portable `.agents/skills` skill can be loaded by BOSSMAN and OpenCode.
11. Skill Forge can propose a new skill from a completed run.
12. permission-expanding skill edits require approval.
13. Terminal page runs a sandbox command and streams output.
14. agent cannot escape approved workdir in sandbox mode.
15. scoped external root works only when explicitly allowed.
16. host/package/system commands hit the configured approval level.
17. OpenCode adapter can execute one test coding task in a worktree.
18. browser + terminal + MCP + OpenCode events appear in canonical BOSSMAN activity log.
