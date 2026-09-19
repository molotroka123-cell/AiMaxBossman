---
name: test-driven-development
description: "Use for a narrow bug fix or behavior change requiring a regression. Russian triggers: напиши тест, негативный тест, регрессия, исправь баг, red green."
license: MIT
compatibility: BOSSMAN portable agent skills; instructions only
metadata:
  owner: bossman
  version: "1.0.0"
  adaptation: "Bossman-specific, modified from upstream; not the full upstream package"
  upstream: "obra/superpowers"
  upstream_commit: "b36e0829c6d0140e93cfef2ca599b1b07d4a7797"
  release_scope: "freeze"
---

# Test-driven changes for Bossman

## Red, green, then clean up
1. Name the production behavior and the specific broken implementation the test
   must reject. Prefer a real service, real temporary files and observable
   effects over a mock that simply repeats its configured answer.
2. Add the smallest test and run it before the fix. Record a behavioral failure,
   not a missing import or an unrelated fixture error. For existing patches,
   use an isolated baseline worktree or controlled mutation as a negative
   control. Never delete user or integrator code to satisfy a TDD ritual.
3. Make the smallest implementation change. Do not add a parallel API, a new
   framework or a broad refactor while fixing the regression.
4. Run the test, neighboring tests and relevant platform checks. Keep the
   acceptance assertion unchanged unless the contract itself is demonstrably
   wrong; document that decision separately.
5. Refactor only with the tests green. Report the exact command, exit status,
   tested source and any skips. A new skip is not a successful fix.

## Required contrasts
- Happy path plus wrong owner, stale revision, invalid arguments or denial.
- For a refusal, assert zero externally visible effects, not just an error.
- For retry/resume, inspect durable state across a fresh process; count effects.
- For UI work, interact through the control the owner uses. A direct API write
  can test the service but cannot stand in for a broken button.
- For generated files, reopen and inspect content. Existence alone is too weak.

Use deterministic clocks and synchronization barriers for race tests where
possible. Do not claim a mock test proves Windows input, model performance,
private-network isolation or the absence of every race.

## Deliverable
One focused regression per behavior, negative-control evidence, a minimal fix,
positive and refusal-path results, and explicitly named untested environments.

## Authority and execution boundary
This file is procedural guidance, not an executable or an authorization grant.
Current Bossman policy, privacy, budget and owner-control gates remain
mandatory. Never print credentials, auto-install dependencies, transmit private
context, change the active owner session or enable standing autonomy from a
skill. Use only task-relevant skills; missing tools or evidence means NOT_RUN.

## Attribution and changes
Source: https://github.com/obra/superpowers/blob/b36e0829c6d0140e93cfef2ca599b1b07d4a7797/skills/test-driven-development/SKILL.md

Upstream authors: Jesse Vincent / obra. Adapted for Bossman on 2026-09-07 under MIT.
Condensed; preserve existing work instead of upstream delete-and-restart instruction. Reuse existing pytest/node/browser infrastructure rather than install new tooling.

License and notices: `docs/skills/licenses/SUPERPOWERS-MIT.txt` and `docs/skills/THIRD_PARTY_NOTICES.md`
(repository-root paths). Original license: https://github.com/obra/superpowers/blob/b36e0829c6d0140e93cfef2ca599b1b07d4a7797/LICENSE
