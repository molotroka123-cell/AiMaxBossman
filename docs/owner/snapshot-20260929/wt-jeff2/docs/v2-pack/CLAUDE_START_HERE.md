# BOSSMAN V2 — CLAUDE CODE START HERE
## Prewritten implementation pack — designed to save Claude/Fable session limits

Target repository:

`molotroka123-cell/AiMaxBossman`

Repository state this pack was prepared against:

`claude/bossman-control-v03-43igbk`

Do **not** begin by re-deriving the architecture from scratch. The repository was already audited before this pack was created.

---

# 1. What exists today

The current Command Center is a real MVP, not only a UI:

- provider/model registry
- local OpenAI-compatible providers
- Anthropic provider
- persistent tasks/runs
- queue lease/heartbeat/recovery
- scheduler
- pause/resume/stop/retry
- approvals database/API
- CPU/RAM/GPU metrics
- WebSocket event stream
- encrypted provider keys

The older `bossman-core` contains stronger agent tooling:

- real tool-call loop
- filesystem tools
- sandbox shell/tests
- git tools
- approvals/permissions
- context handling
- LiteLLM gateway
- OpenRouter fallback

Important current limitations:

1. Command Center engine and `bossman-core` tool runtime are not yet canonicalized into one runtime.
2. Command Center worker execution is still too sequential for a true swarm.
3. Command Center model adapter is OpenAI-compatible but does not yet expose the full tool-calling / structured-output surface.
4. OpenRouter is already reachable architecturally, but the full remote catalog metadata is not synchronized.
5. Native MCP Manager is not present yet.
6. Native Skill Library is not present yet.
7. Existing shell/filesystem deliberately stay inside the agent workdir.
8. Browser/Playwright control is not in the repository yet; prewritten code is included here.

---

# 2. Your task

Use this package as implementation scaffolding for the 15-agent V2 pass.

Do not blindly overwrite current repository files.

The code under:

`command-center/bcc/v2/`

is **prewritten reusable logic**. Review it, test it, then wire it into the canonical current services/API/database.

The portable skills under:

`.agents/skills/`

should be copied into repository root and loaded by both BOSSMAN and OpenCode.

---

# 3. Locked architecture decisions

Do not spend tokens debating these unless a failing test proves one wrong.

## Models

- BOSSMAN owns top-level model routing.
- OpenRouter is a first-class cloud provider/gateway.
- OpenRouter provider-routing may operate *inside* a BOSSMAN route.
- Remote model metadata must be synchronized rather than hardcoded.
- Advertised capabilities and verified capabilities are stored separately.

## Browser

- Playwright is primary.
- DOM-first, vision second.
- Human Take Over blocks agent browser actions.
- login/upload/download/submit default ASK.
- payment/wallet/bank-transfer default DENY.
- Full desktop computer control is a separate security layer.

## Terminal

Agent 07 becomes:

**Worktree + Workspace + Terminal + OpenCode Runtime**

Three modes:

1. sandbox — default
2. project_host — scoped roots, approval-gated
3. system_admin — explicit privileged mode

Never create a global "whole computer = allow" switch.

## MCP

Agent 10 becomes:

**Skill Library + Skill Forge + MCP Hub**

MCP tools enter the canonical BOSSMAN Tool Registry and remain permissioned.

Do not advertise every MCP tool to every model; only assigned tools enter the model context.

## Skills

Canonical portable location:

`.agents/skills/<skill-id>/SKILL.md`

Also discover/import compatible OpenCode/Claude locations.

Skill Forge proposes skills from repeated workflows, but does not create hundreds of trivial prompt files.

## OpenCode

OpenCode is an execution engine under BOSSMAN, not the owner of BOSSMAN missions.

Use the OpenCode HTTP server instead of screen-scraping its UI.

BOSSMAN remains canonical for:

- missions
- tasks
- agents
- budgets
- permissions
- routing
- resources
- history
- approvals

---

# 4. Integration order

Use this order to reduce conflicts:

1. Introduce migrations/Alembic or a centralized migration strategy.
2. Shared V2 contracts.
3. Portable Skill Library.
4. OpenRouter catalog + capability probes.
5. Canonical Tool/Permission Registry.
6. MCP Hub.
7. Terminal/Worktree/OpenCode runtime.
8. Browser runtime.
9. Resource Brain.
10. Smart Model Router.
11. Mission + KPI.
12. Governor.
13. Reviewer Gate.
14. Replay/Fork.
15. Self-Healing.
16. Agent Map.
17. Mobile Command Mode.
18. UI redesign/integration.
19. full E2E/failure injection.

---

# 5. First tests to run

The package includes pure-logic tests under:

`command-center/tests/v2/`

They are intentionally independent of the live DB where practical.

Run:

```bash
cd command-center
pytest -q tests/v2
```

Then wire services to the actual application and add repository-level integration tests.

---

# 6. Required OpenRouter result

From the BOSSMAN UI:

1. Add OpenRouter API key.
2. Synchronize remote catalog.
3. Show model name, context, input/output modalities, pricing and supported parameters.
4. Pin a model into BOSSMAN.
5. Live-probe chat.
6. Live-probe tools when advertised.
7. Live-probe structured output when advertised.
8. Live-probe vision when advertised.
9. Store advertised vs verified capabilities.
10. Never destroy aliases/history on refresh.

---

# 7. Required Terminal result

From BOSSMAN UI:

- open a terminal session
- show exact cwd and exact command
- stream stdout/stderr
- Stop/Kill
- Take Over / stdin when supported
- sandbox by default
- project-host only for configured roots
- package install / Docker changes / git push => ASK
- admin/system => ASK or DENY by policy
- secrets and wallet stores stay outside ordinary model-visible roots

---

# 8. Required Skills result

BOSSMAN discovers:

- `.agents/skills`
- `.opencode/skills`
- `.claude/skills`
- configured user roots

Do not recursively search the entire computer.

Skill Library shows:

- source
- version/fingerprint
- description
- agents using it
- permissions
- run history
- diff when Skill Forge proposes an update

---

# 9. Required MCP result

From UI:

- add local or remote MCP server
- status/health
- list tools/resources/prompts
- assign selected tools to an agent
- AUTO / ASK / DENY
- MCP call appears in canonical event history
- MCP process failure is detected
- only assigned MCP tools are sent to the LLM

---

# 10. Required OpenCode result

Start `opencode serve` on localhost with authentication.

BOSSMAN can:

- health-check it
- attach a worktree/project
- create/list a session
- abort a session
- fork a session
- fetch session diff
- stream/collect events in the integration layer
- store OpenCode session ID against BOSSMAN run/task

Do not give OpenCode unrestricted host paths by default.

---

# 11. Definition of Done

No feature is DONE because a page exists.

For every feature require:

- backend/runtime path
- persistence where relevant
- real UI action
- success test
- failure test
- event/audit trail
- permission behavior
- restart/recovery behavior where relevant

Use the existing verified 15-agent master prompt in `prompts/`.

---

# 12. Files in this pack

### Prewritten runtime logic

`command-center/bcc/v2/`

### Tests

`command-center/tests/v2/`

### Portable first skills

`.agents/skills/`

### Architecture/integration docs

`docs/`

### Existing master prompts

`prompts/`

### UX reference images

`ux-references/`

---

# FINAL NOTE

Do not waste a Claude session rewriting these modules merely for stylistic preference.

Change them only when:

- integration requires it
- repository conventions require it
- a test exposes a bug
- security review exposes a weakness

The goal is to spend Claude tokens on **integration and verification**, not retyping already-decided scaffolding.


## Extra routine-saving code

Also read `docs/ROUTINE_CODE_ADDED.md` before writing V2 boilerplate.
