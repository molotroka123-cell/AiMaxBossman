# Phase 1: Guarded OpenHands Sidecar + Client

**Date:** 2026-09-08  
**Authoritative implementation:** `b3924b65c25686560b54265d66188659908a1b24`  
**Status:** REPOSITORY IMPLEMENTED / LIVE PROVIDER NOT_RUN

## Current implementation

The historical Phase-1 sidecar stub has been superseded.

Bossman now ships a real OpenHands SDK sidecar using the public SDK shape:

`LLM -> Agent -> Conversation`

with `TerminalTool`, `FileEditorTool`, and `TaskTrackerTool`.

The Bossman client communicates with the sidecar over a one-request/one-response JSON-stdio protocol (`bossman.openhands.v1`). Bossman independently derives the post-run Git delta; sidecar self-report is not trusted as evidence.

## Trust boundary

OpenHands is an **untrusted coding worker**, not Bossman's orchestrator.

- feature flag defaults OFF;
- only an explicit sanitized ProblemBundle is materialised for the worker;
- the owner checkout is not passed to OpenHands;
- the disposable OpenHands repository has no remote;
- dirty state, HEAD rewrite/commit/reset, Git config modification, and remote creation fail closed;
- allowed/protected path policy is enforced after execution from independently derived Git state;
- OpenHands cannot complete a mission, approve its own patch, push, or deploy;
- the existing `TeacherFallback` + `PatchVerifier` remains authoritative;
- the OpenRouter credential is removed from the sidecar process environment before terminal tools execute;
- raw model reasoning is not persisted as learning evidence.

## Runtime

Bossman may remain Python 3.11+. OpenHands runs in a separate Python 3.12+ environment.

Install the pinned compatible sidecar stack from:

`bossman-core/requirements-openhands.txt`

Configure:

```bash
BOSSMAN_OPENHANDS_CODE_FALLBACK=1
BOSSMAN_OPENHANDS_COMMAND="python3.12 scripts/openhands_sidecar.py"
BOSSMAN_OPENHANDS_MODEL="openrouter/anthropic/<claude-model>"
OPENROUTER_API_KEY="..."
```

## Evidence

Focused isolated contract evidence before the authoritative code push: **12/12 PASS**.

This included real subprocess boundaries and temporary Git repositories, scope/protected-path denial, dirty/remote refusal, environment leakage protection, contract tamper denial, prevention of hidden changes via agent Git commit/reset, SDK-shape execution via an offline fake SDK, feature-flag wiring, OpenRouter credential filtering, and preservation of the pre-existing hermetic teacher sandbox.

## Remaining external acceptance

`LIVE_OPENHANDS_OPENROUTER = NOT_RUN`

A real Claude/OpenRouter call still requires the owner's provider credential and an installed Python 3.12+ OpenHands environment. Do not report live acceptance until that run is executed and its post-state is verified.
