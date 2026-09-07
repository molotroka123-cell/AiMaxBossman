# V6 OpenHands Integration — Reconciled Completion Report

**Date:** 2026-09-08  
**Branch:** `v6/velocity-phase0-baseline-20260907`

## Decision

Repository-side guarded integration is complete enough for **live acceptance**, not for an unconditional production-ready claim.

Authoritative implementation SHA:
`b3924b65c25686560b54265d66188659908a1b24`

The old 15/15 “100% / production-ready” claim in this document was based on mock/stub-era coverage and is historical. It is superseded by the current audit and hostile contract tests.

## Architecture delivered

```text
Bossman mission / TeacherFallback
        |
        v
OpenHandsTeacherClient
        |
        v
sanitized standalone temporary Git repo (NO REMOTE)
        |
        v
OpenHands SDK sidecar, Python 3.12+
        |
        v
Claude via OpenRouter
        |
        v
candidate filesystem state
        |
        v
Bossman independently derives Git delta
        |
        v
UNTRUSTED candidate patch
        |
        v
existing PatchVerifier + acceptance/regression/security/evidence gates
```

OpenHands is a coding executor. Bossman remains the orchestrator and authority.

## Repository evidence

Focused local guarded contract suite before authoritative code push: **12/12 PASS**.

This replaces the historical mock-only pass count as the relevant OpenHands evidence.

Exact code-SHA GitHub workflows were triggered, but rapid follow-up commits superseded/cancelled some long-running workflows. Therefore this report does **not** claim a complete exact-SHA CI green result for `b3924b65...`.

## Security posture

Implemented:

1. explicit allowlist/protected paths;
2. sanitized ProblemBundle only;
3. standalone temporary Git repo with no remote;
4. clean-state prerequisite;
5. independent Git evidence;
6. deny agent HEAD/config/remote manipulation;
7. deny deletion/binary candidate patches;
8. feature flag OFF by default;
9. OpenRouter-only production builder;
10. credential removed from process environment before terminal tools execute;
11. no OpenHands push/deploy/self-approval/mission-completion authority;
12. existing hermetic teacher sandbox and independent PatchVerifier preserved.

Important limitation: local OpenHands SDK execution is still process-level, not an OS/container security boundary. For stronger isolation, migrate execution to OpenHands Agent Server/container workspace while keeping Bossman's verification model unchanged.

## Runtime dependencies

Use the modern SDK packages from `bossman-core/requirements-openhands.txt` in a separate Python 3.12+ environment.

Do not follow historical instructions to install `openhands-ai` or to replace `run_openhands_agent()` manually; those instructions are superseded.

## Remaining acceptance

`LIVE_OPENHANDS_OPENROUTER = NOT_RUN`

Required to close it:

1. install the pinned OpenHands sidecar environment on the owner machine/container;
2. provide `OPENROUTER_API_KEY` without committing/logging it;
3. choose an explicit `openrouter/anthropic/...` Claude model;
4. enable `BOSSMAN_OPENHANDS_CODE_FALLBACK=1` for the test only;
5. execute a disposable coding task;
6. verify actual file effect, independent diff, PatchVerifier decision, provider/model identity, cost/latency if available, and absence of unauthorized effects;
7. keep the feature flag OFF by default until live acceptance and benchmark evidence are satisfactory.

## Final status

- `REPO_OPENHANDS_READY = YES`
- `GUARDED_CONTRACT_TESTS = PASS (12/12 focused)`
- `LIVE_OPENHANDS_OPENROUTER = NOT_RUN`
- `PRODUCTION_OS_ISOLATION = PARTIAL / process-level local SDK`
- `PRIMARY_CODER_SWITCH = NOT_YET; benchmark first`

This is the canonical interpretation of “complete” for the current branch.
