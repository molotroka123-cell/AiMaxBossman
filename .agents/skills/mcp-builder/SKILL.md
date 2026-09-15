---
name: mcp-builder
description: "Use for an authorized MCP connector or tool/schema integration task. Russian triggers: MCP, подключить сервис, инструмент агента, схема аргументов, structured output."
license: Apache-2.0
compatibility: BOSSMAN portable agent skills; instructions only
metadata:
  owner: bossman
  version: "1.0.0"
  adaptation: "Bossman-specific, modified from upstream; not the full upstream package"
  upstream: "anthropics/skills"
  upstream_commit: "41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f"
  release_scope: "postfreeze"
---

# MCP and tool contracts for Bossman

## Design within the existing system
Reuse Bossman's gateway, registry, vault, policy, budgets, audit and effect
receipts. Do not create a second permission engine or bypass those services.
Read the current protocol and installed SDK documentation before coding; this
skill does not freeze an SDK API or authorize package installation.

Define tools around concrete user tasks. Prefer clear verbs and concise input
and output schemas to an enormous undifferentiated tool list. Distinguish
read-only discovery from mutating actions. Paginate whole datasets with honest
counts and error states. Tool annotations are descriptions, not authorization.

## Implementation contract
- Validate types, bounds, unknown fields and ownership before dispatch.
- Resolve credentials through the existing vault; never return, log or place
  them in a URL. Restrict endpoints and redirects through current egress policy.
- Return structured results with status, sanitized error/recovery guidance,
  external identifiers and evidence references where relevant.
- Give retries a deadline and budget. A network timeout after a mutation is
  ambiguous; use durable idempotency/reconciliation rather than blind retries.
- Bind approval to the actor, target, arguments, current revision and effect.
  Recheck at dispatch. A skill, tool result or observation cannot grant rights.

## Evaluate discoverability and behavior
Use the same actual model/configuration on a held-out set of realistic tasks.
Measure tool selection, schema/argument accuracy, multi-step completion and
error recovery separately. Include wrong owner, stale approval, duplicate
submission, redirect, invalid key and offline cases. Make changes in local
fixtures or an authorized sandbox only. Mocks prove contracts, not service
availability or live-model skill. Keep raw/system/context/full lanes honest:
listing tool names in a prompt is not a real tool execution loop.

## Deliverable
Minimal connector code, input/output contracts, focused tests, bounded
end-to-end evidence, dependency/permission changes requiring approval, and
explicit NOT_RUN rows for unavailable services. No automatic deployment.

## Authority and execution boundary
This file is procedural guidance, not an executable or an authorization grant.
Current Bossman policy, privacy, budget and owner-control gates remain
mandatory. Never print credentials, auto-install dependencies, transmit private
context, change the active owner session or enable standing autonomy from a
skill. Use only task-relevant skills; missing tools or evidence means NOT_RUN.

## Attribution and changes
Source: https://github.com/anthropics/skills/blob/41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f/skills/mcp-builder/SKILL.md

Upstream authors: Anthropic. Adapted for Bossman on 2026-09-07 under Apache-2.0.
Condensed to Bossman-compatible connector workflow; no new SDK, inspector command, transport or server is installed. Removed missing reference dependencies and default framework migration.

License and notices: `docs/skills/licenses/Apache-2.0.txt` and `docs/skills/THIRD_PARTY_NOTICES.md`
(repository-root paths). Original license: https://github.com/anthropics/skills/blob/41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f/skills/mcp-builder/LICENSE.txt
