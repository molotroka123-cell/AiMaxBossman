# Зоны «Агенты и оркестрация», «Навыки», «Плагины и коннекторы» — подготовка к owner-test

Date: 2026-10-05. Branch: `goal/bossman-self-improvement-tree-20261005`. Tested first in
the separate worktree branch `lab/zones-agents-skills-plugins-20261005`, then ported.

## What was broken (reproduced on the current code before the fix)

| Zone | Before | After |
|---|---|---|
| Навыки | 48 SKILL.md texts, **0 runtime code, 0 verification tests** (3 SKILL.md silently dropped by the cap) | skill engine (`skills.py`, `v2/skill_library.py`, `skill_catalog.py`, `skill_evaluation.py`) in scope first, 8 tests |
| Плагины | `plugins.py` only, 1 test; `test_plugin_security` not found (name mismatch) | + MCP hub/runtime, `plugin_security.py`, OSS inventory; 12 tests incl. security and MCP boundary |
| Агенты | 13 modules; all 8 test slots went to rave/coding; missions/task exchange unverified | 17 modules (+ agent graph, orchestration schema, agent lab); 12 tests, round-robin across modules |

Zone work (`POST /api/capability-tree/work`) now:
1. puts code (`.py/.js`) before prose, so the file cap never removes the runtime;
2. finds tests by name **and** by import (`from bcc.features import plugins`, `features.plugins`);
3. takes each module's own tests first, round-robin, then importers; max 12;
4. excludes Playwright suites from import matches (heavy and unrelated to the zone).

`test_ux2_wizards` was red on this goal branch (8 errors): the tree's own auto-open polls
`GET /api/evolution/status`, which the wizard allowlist did not list. That one read-only path
was added; every other API path is still rejected.

## Proof (lab worktree, Linux, Python 3.11)

- union of the three zones' verification tests + `test_capability_tree.py` + `test_ux2_wizards.py`:
  **483 passed, 0 errors** (before the allowlist fix `test_ux2_wizards` had 8 errors on the base too);
- `test_capability_tree.py`: 20 passed; the two new scope tests fail on the old code (negative control).

Not run: owner machine, live models, real MCP servers/OAuth connectors. Zone work still produces
only a candidate; it enters the project only after the owner's Apply.

## Open source — vetted references (PyPI metadata 2026-10-05)

| Project | License | Use in Bossman |
|---|---|---|
| openai/openai-agents-python | MIT | handoffs/guardrails/tracing pattern for nl_orchestra, task_exchange |
| langchain-ai/langgraph | MIT | durable checkpoint/interrupt pattern for missions + WAIT_APPROVAL |
| pydantic/pydantic-ai | MIT | typed tool contracts for skill contracts/evaluation |
| crewAIInc/crewAI | see repo | role/process UX reference for fleet/swarm |
| modelcontextprotocol/python-sdk (`mcp`) | MIT | official SDK under mcp_runtime |
| PrefectHQ/fastmcp | Apache-2.0 | own Bossman connectors as MCP servers |
| modelcontextprotocol/servers | see repo | reference servers for the plugins-mcp live test |
| ComposioHQ/composio | Apache-2.0 | OAuth connectors — owner approval only |
| temporalio/sdk-python | MIT | durable workflow reference for mission recovery |

Already on the tree and kept: anthropics/skills, obra/superpowers, huggingface/skills,
trailofbits/skills, ag2ai/ag2, VoltAgent/awesome-agent-skills. These are references, not installed
dependencies: adoption = candidate branch + test + license check + owner approval.

## Tomorrow's test

Open the tree → zone → «Работать». The report should name 8–12 tests. A verified candidate stays
a candidate until Apply.

North Star: `SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT` (unchanged). Better zone scoping is
infrastructure, not `SELF_REPAIR_SINGLE_CYCLE_PASS`. Terminal Run contract unchanged: CLI, UI and
Telegram use the same backend, tasks and verification.
