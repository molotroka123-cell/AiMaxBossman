# Bossman Token Shunt Routing — Technical Specification

Date: 2026-09-08
Branch: `feat/token-shunt-routing-20260908`

## Goal

Adopt the proven Spotify `shunt` pattern inside Bossman: route I/O-heavy and predictable work to cheap/fast workers while reserving frontier models for debugging, architecture, security, effectful decisions and non-trivial reasoning.

This is an optimization layer, never an authority layer.

## Upstream reference

Spotify's open-source `spotify/portal-ai-plugins` ships `shunt`, a Claude Code plugin with three layers:

1. PreToolUse hooks enforce routing for expensive reads.
2. Scripts invoke cheap AiKA worker modes.
3. Skills describe when/how to delegate.

Published benchmarks report 82–94% token savings on bulk-read scenarios, mean ~90%. The pattern explicitly does not delegate debugging, architecture, editing that needs exact context, or small files.

Bossman should reuse the architecture/policy, not hard-depend on Spotify Portal.

## Bossman architecture

`frontier orchestrator -> shunt policy -> cheap worker -> typed compact artifact -> frontier verifier/reasoner`

Workers are replaceable. Initial backends may be:

- local small/medium model on the owner's machine;
- Gemini Flash-class API worker;
- other cheap provider already present in Bossman's provider registry;
- optional Spotify Portal/AiKA adapter if the owner later configures Portal.

No worker may acquire execution authority by being selected by the shunt.

## Initial worker roles

### bulk-reader

Use for broad file discovery/understanding where exact source text does not need to enter frontier context.

Input:
- question;
- explicit file list;
- repository identity and source SHA;
- size limits.

Output:
- structured bullets;
- exact file paths;
- symbol/section references where known;
- uncertainty/missing-context field;
- source SHA/digest binding.

### code-writer

Use only for predictable boilerplate:
- test scaffolding from an existing reference;
- config stubs;
- repetitive type/schema stubs;
- mechanical docs formatting.

Output is always an untrusted proposal. It may write only to an isolated proposal workspace/sandbox. Existing Bossman diff/evidence/approval/application gates remain authoritative.

## Hard routing policy

Default bulk-read threshold: 350 lines, configurable and benchmarked.

Delegate:
- untargeted reads of large files;
- multi-file summarization;
- repetitive boilerplate generation;
- low-risk documentation extraction;
- pattern matching across large corpora.

Do NOT delegate:
- debugging/root-cause analysis;
- architectural decisions;
- security/privacy/permission/effect logic;
- AT-01/AT-03/canary/rollback/release authority;
- merge-conflict semantic resolution;
- editing where exact lines are needed;
- small/targeted reads;
- final code review/verdict;
- owner approvals;
- live financial execution or trading decisions.

Targeted reads with explicit ranges must bypass the bulk-read shunt.

## Fail-closed rules

If the worker fails, times out, returns malformed output, loses source binding, or reports insufficient context:

- do not invent a summary;
- do not mark the task complete;
- either retry within a small budget or fall back to frontier targeted reads;
- record why the fallback happened.

Worker output can reduce context size, but cannot replace verifier evidence.

## Context-integrity protection

A cheap summary can hide a subtle bug. Therefore:

- summaries are advisory;
- frontier model must directly read exact relevant ranges before non-trivial edits;
- frontier must directly inspect security/authority-sensitive code;
- any summary used for a patch must retain source SHA and file digest references;
- if source changes after summarization, the summary becomes stale and must not be used as current evidence.

## Cost/quality router

Routing score should consider:

- task class;
- file size / projected tokens;
- latency overhead;
- worker health;
- model benchmark score for the capability;
- privacy policy;
- local resource pressure;
- cost ceiling;
- source sensitivity.

Never route solely because a model is cheaper.

## Telemetry

For every delegated operation record:

- source SHA;
- task id;
- worker/model id;
- task class;
- input bytes/estimated tokens;
- worker output tokens;
- frontier tokens avoided estimate;
- latency;
- fallback/retry reason;
- verifier outcome;
- whether direct frontier reread was required.

No hidden chain-of-thought is stored.

## Acceptance benchmark

Use a fixed corpus representative of AiMaxBossman:

1. single 4k+ line file understanding;
2. source + tests pair;
3. cross-module multi-file question;
4. test scaffold generation;
5. config/schema boilerplate;
6. subtle bug case that MUST stay on frontier;
7. architecture case that MUST stay on frontier;
8. security/effect case that MUST stay on frontier;
9. targeted edit requiring exact range;
10. stale-summary-after-source-change negative control.

Compare BASELINE frontier-only vs SHUNT.

Measure:
- frontier input/output tokens;
- total tokens across all models;
- monetary cost;
- wall time;
- answer correctness;
- patch correctness;
- verifier pass rate;
- wrong-route rate;
- fallback rate.

Target:
- >=70% frontier-token reduction on eligible bulk-read corpus initially;
- stretch target ~90% where Spotify-like workloads permit it;
- 0 architecture/security/effect tasks wrongly delegated;
- no statistically meaningful correctness regression;
- no increase in unverified completion.

Do not claim 90% globally from Spotify's benchmark. Bossman must measure its own corpus.

## Integration order

Phase 0: benchmark only, no routing behavior change.

Phase 1: advisory router + telemetry, shadow mode.

Phase 2: enforce large untargeted read shunt behind feature flag `token_shunt_v1`, default OFF.

Phase 3: add code-writer proposal worker only after bulk-reader passes quality gates.

Phase 4: prefer a suitable local worker on the target 128GB machine when measured faster/cheaper enough; otherwise use configured cheap API worker.

Phase 5: canary rollout with automatic rollback on correctness/latency regression.

## Protected Bossman invariants

Do not modify or bypass:

- permission/budget/privacy/effect gates;
- AT-01 / AT-03;
- canary/N8/rollback authority;
- World State verifier boundaries;
- OpenHands isolation;
- provider health/circuit breaker;
- existing approval semantics;
- release evidence rules.

The shunt chooses who performs low-risk cognitive/I/O work. It never decides what is authorized to happen in the world.
