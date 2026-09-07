# OpenHands Integration — Current Truth

**Date:** 2026-09-08  
**Branch:** `v6/velocity-phase0-baseline-20260907`

## Canonical status

`REPO_OPENHANDS_READY = YES` for the guarded repository implementation.

`LIVE_OPENHANDS_ACCEPTANCE = NOT_RUN` until the owner runs a real OpenHands SDK environment with a real OpenRouter credential/model and verifies the resulting post-state.

Authoritative guarded code SHA:
`b3924b65c25686560b54265d66188659908a1b24`

Authoritative audit/docs SHA before this reconciliation:
`253a2b82d51e2a706897684c03fc559ac95c7247`

## What is genuinely implemented

- real JSON-stdio OpenHands SDK sidecar; historical `run_openhands_agent()` stub superseded;
- current public SDK flow `LLM -> Agent -> Conversation`;
- `TerminalTool`, `FileEditorTool`, `TaskTrackerTool`;
- Claude/OpenRouter-only production builder (`openrouter/...` model required);
- independent feature flag `BOSSMAN_OPENHANDS_CODE_FALLBACK`, OFF by default;
- sanitized standalone temporary Git repository with no remote;
- owner checkout not supplied to OpenHands;
- Bossman independently derives the Git delta instead of trusting agent file claims;
- allowed/protected paths fail closed;
- dirty checkout, existing remote, agent HEAD rewrite/commit/reset, Git config modification, and new remote fail closed;
- deletion/non-file/binary candidate changes rejected by the teacher adapter;
- existing hermetic Claude teacher sandbox restored/preserved;
- existing `TeacherFallback` budget/sanctions/memory + independent `PatchVerifier` remain authoritative;
- OpenHands cannot self-complete a mission, push, deploy, or approve its own output;
- Bossman environment is not inherited wholesale by the sidecar;
- OpenRouter API key is removed from the sidecar environment before OpenHands terminal tools run;
- no raw chain-of-thought persistence.

## Focused evidence

Local isolated sandbox before code push:

`12/12 PASS`

Coverage included real subprocesses and temporary Git repositories, scope violations, protected paths, dirty state, remotes, environment leakage, contract tampering, hidden changes through Git commit/reset, sidecar error redaction, SDK-shape execution with an offline fake SDK, flag/wiring tests, provider-env filtering, and preservation of the old hermetic teacher sandbox.

GitHub workflows on the exact code SHA were subsequently superseded by rapid follow-up commits; observed completed runs included successes and cancellations, not a complete exact-SHA green certification. Do not rewrite that history as “all CI green.”

## Dependency truth

OpenHands requires Python >=3.12. Bossman's Python 3.11 compatibility is preserved by using a separate sidecar environment.

Use `bossman-core/requirements-openhands.txt`. The repository pins a known-compatible matched SDK/tools stack rather than mixing package minors.

Do **not** install the historical `openhands-ai` package for this adapter. The integration uses the modern `openhands-sdk` + `openhands-tools` packages.

## Worktree clarification

`IsolatedWorktree` is a useful trusted developer utility, but a linked Git worktree is not the security boundary for untrusted OpenHands. Production wiring uses a sanitized standalone temporary repository. For stronger OS isolation, migrate the same adapter to OpenHands Agent Server/container workspace later; Bossman must still retain final verification authority.

## What is still not proved

- real Claude call through OpenRouter: NOT_RUN;
- real provider cost/latency/fallback accounting: NOT_RUN;
- owner Windows OpenHands environment: NOT_RUN;
- container/Agent-Server hard isolation: NOT IMPLEMENTED in the local SDK path;
- benchmark showing OpenHands beats Bossman native coder: NOT_RUN.

## Release posture

- `OPENHANDS_IMPLEMENTATION = PRESENT`
- `OPENHANDS_GUARDED_CONTRACT = PASS (focused 12/12)`
- `OPENHANDS_FEATURE_FLAG_DEFAULT = OFF`
- `OPENHANDS_DIRECT_PUSH_DEPLOY = DENIED`
- `OPENHANDS_AUTO_MISSION_COMPLETION = DENIED`
- `OPENHANDS_OWNER_CHECKOUT_AS_WORKSPACE = DENIED BY DESIGN`
- `OPENHANDS_LIVE_OPENROUTER = NOT_RUN`

The next real acceptance step is one owner-authorized credentialed run, not another mock or another architecture rewrite.
