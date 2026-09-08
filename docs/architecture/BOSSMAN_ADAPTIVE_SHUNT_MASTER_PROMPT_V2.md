# BOSSMAN Adaptive Token Shunt — Master Prompt V2

Date: 2026-09-08

## Role
You are implementing a Bossman-native adaptive model-routing and context-compression layer inspired by Spotify Shunt, but generalized to ALL Bossman frontier models, API providers, local models and agent runtimes. This is not Claude-specific.

## Goal
Reduce expensive/frontier-model context and token consumption on low-reasoning, I/O-heavy work without reducing correctness, safety, intelligence retention, provenance or release authority.

Target after measurement:
- >=70% frontier-token reduction on eligible workloads;
- stretch ~90% on bulk-read/repo-search workloads;
- zero protected-class wrong delegation;
- local-first mode should reduce API spend further when a qualified local worker exists.

Never copy Spotify's benchmark as Bossman's measured result.

## Architecture
FRONTIER/ORCHESTRATOR -> SHUNT GATE -> specialized cheap/local worker -> typed source-bound result -> verifier -> frontier escalation when required.

Worker lanes:
1. BULK_READER — large files/logs.
2. REPO_SEARCHER — multi-file discovery/search.
3. CODE_WRITER — boilerplate, tests, config, schemas/types only.
4. DOCS_WORKER — documentation extraction/summarization.
5. LOCAL_WORKER — qualified local model lane for any eligible capability.

Frontier/orchestrator models retain debugging, architecture, security, semantic merge resolution, non-trivial edits and authority-sensitive decisions.

## Hard interception, not prompt etiquette
Implement enforceable pre-tool routing for untargeted bulk reads. A CLAUDE.md/system-prompt recommendation is insufficient.

Intercept at least:
- direct full-file Read above threshold;
- shell cat/less/more of large files;
- untargeted head/tail equivalents that effectively dump large context;
- large multi-file concatenation/dumps;
- large log ingestion.

Explicit narrow range reads remain frontier-eligible.

Initial threshold = 350 lines. Make threshold configurable and later adaptive only from measured telemetry. Never adapt it across a protected task boundary.

## Protected classes — NEVER SHUNT
Never delegate final reasoning/authority for:
- debugging/root-cause analysis;
- architecture/system design;
- security/privacy;
- permissions/approvals/budgets/effect obligations;
- AT-01 / AT-03;
- canary/N8/rollback/promotion/release authority;
- semantic merge conflicts;
- owner approvals;
- financial/trading execution;
- credential handling;
- safety-critical policy;
- edits requiring exact source context.

Workers may gather bounded evidence for these tasks, but the frontier/orchestrator must directly inspect the decisive source ranges before deciding.

## Source-bound worker contract
Every worker result must be typed and include:
- worker/model identity;
- task/capability type;
- repository/source SHA;
- exact file paths;
- file/content digests where applicable;
- source ranges inspected;
- structured result;
- uncertainty/confidence as classification quality only, never truth probability;
- missing context;
- token/cost/latency telemetry;
- verifier status.

If source changes after worker analysis, result becomes STALE and cannot satisfy current evidence.

No worker summary is verifier evidence by itself.

## Bulk Reader
For eligible large reads, return a compact structured answer instead of raw thousands of lines. It must preserve symbol names, paths, relevant ranges, uncertainty and unresolved questions. The frontier model directly reads exact relevant ranges before any non-trivial edit.

## Repo Searcher
Use cheap/local workers for discovery across many files. Return ranked candidate paths + why + exact matching ranges/digests. Discovery is not proof. Frontier verifies decisive matches.

## Code Writer
Only for mechanical generation:
- test scaffolds from an explicit reference;
- config/schema/type stubs;
- repetitive adapters;
- mechanical docs.

Require a reference/template or explicit typed contract. No reference => refuse or escalate.

Generated code is an UNTRUSTED PROPOSAL in an isolated workspace. Worker cannot commit, push, approve, authorize, promote, mark done or modify release evidence.

## Docs Worker
May extract/summarize docs, changelogs, audit text and API documentation. Must preserve citations/path/range provenance. It cannot change audit truth or findings status.

## Local-first routing
Do not hard-code Gemini or any provider. Select workers through Bossman's capability routing.

Preference order when benchmark-qualified:
1. local worker;
2. cheap/fast API worker;
3. stronger API worker;
4. frontier fallback.

Selection considers correctness benchmark, capability, latency, cost, provider health, privacy, available RAM/resource pressure and task type. Cheapness alone never wins.

UNKNOWN/STALE/CONTESTED resource state cannot be treated as sufficient capacity.

## Worker lifecycle and transport hardening
Workers should be bounded/one-shot where practical. Add:
- strict payload/input caps;
- output caps;
- timeout;
- cancellation;
- malformed-output rejection;
- no inherited credentials unless explicitly required;
- no arbitrary command/path/url authority;
- structured serialization only;
- stdout/stderr separation where protocol integrity matters;
- retry budget;
- deterministic frontier fallback.

A worker timeout/failure never becomes mission success.

## Phase 0 — baseline first
Before enforcement, benchmark frontier-only behavior on a frozen SHA:
1. 4k+ line file understanding;
2. source + tests pair;
3. multi-file repo analysis;
4. huge log analysis;
5. repo search/discovery;
6. test scaffold generation;
7. config/schema boilerplate;
8. subtle bug;
9. architecture decision;
10. security/effect problem;
11. semantic merge conflict;
12. targeted exact-range edit;
13. stale-summary-after-source-change;
14. worker timeout/malformed output;
15. local-worker unavailable/resource-pressure case.

Record frontier input/output tokens, total worker tokens, API cost, latency, correctness, verifier result, escalation rate and wrong-route count.

## Phase 1 — shadow router
Run routing classifier in shadow mode. Record FRONTIER_ONLY vs SHUNT decision. No actual delegation changes production behavior yet.

Protected wrong-route count must be ZERO before enforcement.

## Phase 2 — bulk reader + repo search enforcement
Feature flag: token_shunt_v1. Default OFF.

Canary only eligible read/search workloads. Fail closed to frontier fallback. Add hooks/evals for direct Read and shell-based bulk dumping.

## Phase 3 — code/docs workers
Enable only after read/search lane meets correctness and savings gates. Keep isolated proposal workspace and verifier.

## Phase 4 — adaptive/local-first routing
Only after sufficient telemetry. Tune threshold/routing per capability/model/hardware. Never allow adaptation to override protected classes or safety policy.

## Evaluation
Maintain three independent comparisons:
A. FRONTIER_ONLY
B. SHUNT_API
C. SHUNT_LOCAL_FIRST

Measure:
- frontier-token savings;
- total-token savings/overhead;
- API cost;
- wall-clock latency;
- correctness;
- verifier failures;
- stale-result rejection;
- wrong routes;
- protected wrong routes;
- fallback rate;
- context size delivered to frontier;
- context-retention/quality score.

A shunt optimization fails if token savings improve while correctness/intelligence retention materially decreases.

## Required adversarial tests
- protected architecture/security task disguised as summarization;
- huge file containing one subtle concurrency bug;
- stale digest after file mutation;
- source SHA mismatch;
- incompatible repo/worktree identity;
- malformed worker JSON;
- oversized worker output;
- timeout/cancellation;
- worker returns confident unsupported claim;
- worker attempts commit/push/write outside proposal workspace;
- worker requests credentials;
- shell cat/less/more bypass attempt;
- multi-file concatenation bypass;
- targeted read must not be unnecessarily shunted;
- local worker resource state UNKNOWN/STALE/CONTESTED;
- provider circuit open;
- verifier disagreement => frontier escalation;
- restart must not turn stale cached summary into fresh evidence.

## Acceptance gates
For eligible workload corpus:
- target >=70% frontier-token reduction;
- stretch ~90% on bulk workloads;
- protected wrong-route count = 0;
- no correctness regression beyond predefined statistical tolerance;
- no material intelligence-retention regression;
- source binding = PASS;
- stale summary rejection = PASS;
- fallback = PASS;
- canary rollback = PASS;
- no new execution authority = PASS.

Do not optimize benchmarks or lower thresholds to obtain PASS.

## Compatibility / no-break rules
Do not weaken or redesign existing Bossman:
- permissions/approvals/budgets/privacy/effect gates;
- AT-01 / AT-03;
- canary/N8/rollback;
- World State authority/freshness;
- OpenHands isolation;
- signed QA relay / SSRF containment;
- provider health/circuit breaker;
- Video Studio CFR;
- immutable evidence/release authority.

Shunt is an optimization layer, never a second authority layer.

## Final report
BASE_SHA=
SHUNT_SHA=
FEATURE_FLAG=
BULK_READER=
REPO_SEARCHER=
CODE_WRITER=
DOCS_WORKER=
LOCAL_WORKER=
HARD_INTERCEPT_HOOKS=
FRONTIER_TOKEN_BASELINE=
FRONTIER_TOKEN_SHUNT_API=
FRONTIER_TOKEN_SHUNT_LOCAL=
FRONTIER_SAVING_API_PCT=
FRONTIER_SAVING_LOCAL_PCT=
TOTAL_TOKEN_DELTA=
COST_DELTA=
LATENCY_DELTA=
CORRECTNESS_DELTA=
INTELLIGENCE_RETENTION_DELTA=
WRONG_ROUTE_COUNT=
PROTECTED_WRONG_ROUTE_COUNT=
FALLBACK_RATE=
STALE_SUMMARY_TEST=
SOURCE_BINDING=
PAYLOAD_LIMITS=
TIMEOUTS=
CANARY=
ROLLBACK=
OPEN_RELEASE_BLOCKERS=
FINAL_VERDICT=

Do not claim Spotify's ~90% as Bossman's result. Measure Bossman independently.
