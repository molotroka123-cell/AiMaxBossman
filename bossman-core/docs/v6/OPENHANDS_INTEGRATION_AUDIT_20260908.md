# OpenHands Integration Audit — 2026-09-08

## Scope

Branch: `v6/velocity-phase0-baseline-20260907`

Authoritative implementation SHA under audit:
`b3924b65c25686560b54265d66188659908a1b24`

Parent reconciled from concurrent Phase-1 work:
`0c2f38904b2f991d65fe7c9e594bcd35d6218a3c`

## Audit delta

Earlier OpenHands completion documents were not sufficient evidence of a working integration. The branch contained three material problems:

1. `teacher_wiring_patch.py` called an `execute_task()` API that did not match the actual `OpenHandsClient` contract.
2. a later Phase-1 sidecar still implemented `run_openhands_agent()` as a STUB and accepted sidecar-reported `changed_files` as authority.
3. `teacher_sandbox.py` had been replaced by a weaker implementation, regressing the previously established hermetic teacher boundary.

The authoritative implementation fixes all three.

## Current architecture

OpenHands is now an optional untrusted coding backend for the existing `TeacherFallback` pipeline.

It receives only a sanitized `ProblemBundle`, materialised into a disposable Git repository with no remotes. It does not receive the owner checkout. OpenHands may edit that disposable repository using the official SDK tool surface. Bossman then independently derives the Git delta and converts admissible text changes into an untrusted patch proposal. The existing `PatchVerifier` remains the only component that may apply/accept the proposal on the verifier worktree after security, acceptance, regression, freshness and evidence-binding checks.

The original `hermetic_workspace()` implementation for the Claude-style teacher has been restored exactly from the previously verified Git blob. OpenHands does not replace that boundary.

## Security checks added

- explicit allowlist required;
- protected paths fail closed;
- path traversal / absolute / drive paths rejected;
- workspace must start clean;
- workspace must have no Git remote;
- sidecar may not rewrite/commit/reset HEAD;
- sidecar may not change Git config or add a remote;
- Bossman derives changed files rather than trusting agent claims;
- deletion / non-file / binary or non-UTF8 candidate changes are rejected by the adapter;
- failed sidecar execution cannot yield an accepted patch;
- Bossman environment is not inherited wholesale;
- production wiring forwards only `OPENROUTER_API_KEY` / optional `OPENROUTER_BASE_URL`;
- OpenRouter model prefix is mandatory in the Bossman builder;
- sidecar removes the provider credential from its environment before OpenHands `TerminalTool` execution;
- sidecar never emits traceback or exception text to Bossman;
- OpenHands output is marked untrusted and cannot claim mission completion;
- no push/deploy authority is granted.

## OpenHands / OpenRouter implementation

The sidecar uses the current official SDK shape:

`LLM -> Agent -> Conversation`

with `TerminalTool`, `FileEditorTool`, and `TaskTrackerTool`.

Bossman constrains the production path to `openrouter/...` models. OpenHands SDK and tools are installed in a separate Python 3.12+ sidecar environment so the Bossman Python 3.11 contract is not changed.

Matched sidecar dependencies are recorded in `bossman-core/requirements-openhands.txt`.

## Evidence

Local isolated sandbox, before push of the authoritative code SHA:

- core subprocess/Git/sidecar hostile tests: 9 PASS;
- flag/wiring/OpenRouter credential-filter harness: 3 PASS;
- total focused local evidence: **12/12 PASS**.

The local test used actual subprocess boundaries and actual temporary Git repositories. For the SDK contract test, a local fake `openhands` package implemented the documented `LLM`, `Agent`, `Tool`, and `Conversation` API; this proves our sidecar contract without network access or a provider credential.

At audit-authoring time GitHub had started 5 push-triggered workflows for `b3924b65c25686560b54265d66188659908a1b24`; they were still in progress and are therefore **not recorded as green evidence here**.

## Honest remaining gap

`LIVE_OPENHANDS_OPENROUTER = NOT_RUN`

No real OpenRouter credential was available to this audit sandbox, so no real Claude/OpenRouter provider call is claimed.

This is intentionally different from `IMPLEMENTATION = PRESENT` and `GUARDED_CONTRACT_TESTS = PASS`.

## Release posture

- `OPENHANDS_IMPLEMENTATION`: PRESENT
- `OPENHANDS_GUARDED_CONTRACT`: PASS (focused local sandbox)
- `OPENHANDS_EXACT_SHA_CI`: IN_PROGRESS at audit authoring
- `OPENHANDS_OPENROUTER_WIRING`: IMPLEMENTED
- `OPENHANDS_LIVE_PROVIDER_ACCEPTANCE`: NOT_RUN
- `OPENHANDS_AUTO_MISSION_COMPLETION`: DENIED
- `OPENHANDS_DIRECT_PUSH_DEPLOY`: DENIED
- `OPENHANDS_REAL_OWNER_CHECKOUT_ACCESS`: DENIED by design
- `OPENHANDS_FEATURE_FLAG_DEFAULT`: OFF

## Production hardening after live acceptance

The current local SDK Conversation provides process-level isolation. For a stronger production boundary, use the OpenHands Agent Server / remote container workspace while retaining exactly the same Bossman `ProblemBundle -> untrusted candidate -> PatchVerifier` trust model.

Do not weaken the current verification boundary merely to make OpenHands more autonomous.
