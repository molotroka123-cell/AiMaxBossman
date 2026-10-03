# Bossman 2.1 — Autonomous Computer Operator

Status: **DESIGN / NOT YET PROVEN LIVE**  
Scope: documentation for the next implementation and owner-hardware acceptance cycle  
Safety objective: autonomous completion inside a pre-authorized constitution; internal errors must not become harmful external effects

## Owner outcome

The owner gives one natural-language goal, for example:

> Bossman, open YouTube, find the requested video, download the related
> material from another site, process it, and put the verified result on the
> desktop.

Bossman then operates the computer without step-by-step owner confirmation,
recovers from ordinary software and UI failures, verifies the final state, and
stores a reusable recovery-aware workflow.

“Complete in every case” is not a truthful guarantee. The enforceable contract
is: continue through independent permitted strategies until the post-condition
passes or an externally provable blocker is recorded. CAPTCHA, missing
credentials or authority, an offline host, a physical action, and a policy
prohibition are blockers. A changed button, a crashed process, a failed model
attempt, or one broken download route is not.

## Current evidence boundary

| Area | Current status |
|---|---|
| Durable effects, receipts, restart-oriented state | Present in the codebase; independently audited |
| REAL_SANDBOX capability benchmark | Present; 18/18 capability coverage reported by `AUDIT_STATE.md` |
| Learning promotion gates | Present in part: SHADOW/VERIFIED and security evidence |
| Jev/Kev full desktop adapter | Candidate interface; exact implementation and license must be verified |
| Internet research loop | Proposed integration |
| Codex escalation through Bossman CMD | Proposed integration |
| Per-model token telemetry and cache economics | Existing partial telemetry; unified daily auditor proposed |
| Telegram Token Auditor panel | Proposed integration |
| Automatic promotion without per-action confirmation | Proposed; must remain constitution-bounded |
| One-command browser + second site + download + processing mission | Not proven live |
| 24-hour unattended owner-hardware run | Not recorded |

Documentation, unit tests, CI, REAL_SANDBOX, owner-hardware execution, and
unattended LIVE execution are separate evidence classes and must never be
reported as interchangeable.

## Mission state machine

```text
QUEUED
  -> PLANNED
  -> POLICY_CHECKED
  -> EXECUTING
  -> VERIFYING
  -> COMPLETE

EXECUTING | VERIFYING
  -> RECOVERING
  -> RESEARCHING
  -> ESCALATING_LOCAL
  -> ESCALATING_CODEX
  -> EXECUTING

Any active state
  -> SAFE_STOPPED | BLOCKED_PROVEN | ROLLED_BACK | QUARANTINED
```

`COMPLETE` requires machine-observed post-conditions. A model message claiming
success is not evidence.

Every mission persists:

- owner goal and normalized intent;
- expected post-conditions;
- constitution decision and capability scope;
- current plan and last verified checkpoint;
- environment fingerprint, active application/process and URL where allowed;
- attempt signatures, observations, errors and strategy changes;
- effects, idempotency keys and immutable receipts;
- produced/downloaded artifacts and integrity checks;
- restart/resume cursor and rollback point;
- final verifier evidence or exact blocker proof.

## Authority without repeated confirmation

One owner-approved `AUTONOMY_CONSTITUTION` replaces per-step confirmation for
pre-authorized classes. It is versioned, hash-bound, time/scoped, and immutable
to all models and execution adapters.

Automatically permitted examples:

- operate allowlisted applications and isolated browser profiles;
- search public sources and read official documentation;
- create, download, transform and validate files inside scoped directories;
- execute allowlisted CLI/API actions;
- write and test code in an isolated worktree;
- restart Bossman-owned processes;
- update prompts, retrieval, routing and candidate workflows through promotion gates.

Always fail closed:

- transferring money, crypto or other assets;
- exposing credentials, cookies, vault contents or private memory;
- deleting important data without a verified recoverable backup;
- disabling STOP, audit, receipts, watchdog or policy enforcement;
- raising its own authority or changing the constitution;
- irreversible legal/public commitments outside an explicit pre-authorized policy.

Every permitted effect still requires scope, TTL, budget cap, idempotency key,
receipt, observable post-condition and rollback strategy where rollback is
possible.

## Computer-control adapter

Jev, Kev, or a compatible free alternative is an actuator, not the brain.
Adapter selection is benchmark-driven and requires source, license, maintained
version and security review. The adapter may expose screen observation,
semantic UI actions, keyboard/mouse control and application lifecycle, but it
may not own policy, durable memory, secrets, success verdicts or promotion.

Preferred execution order:

1. stable application API;
2. stable CLI;
3. accessibility/semantic UI selectors;
4. vision-grounded UI control;
5. coordinates only as a short-lived fallback.

Bossman Core owns intent, permissions, mission state, evidence and STOP. The
adapter returns observations and effect receipts.

## OpenDots adoption decision

Upstream: `https://github.com/CopilotKit/OpenDots`  
License: MIT  
Decision: **ADOPT AS A BOUNDED UX / AG-UI / ISOLATED-COMPUTER SIDECAR; DO NOT
REPLACE BOSSMAN CORE**.

OpenDots is a useful implementation reference and donor for:

- persistent streaming chat, conversation history and visible tool activity;
- specialist `Dot` surfaces that can map to Bossman/Jeff/worker roles;
- Spaces/pages as an editable artifact and document workspace;
- AG-UI transport for streamed messages, tool calls and agent state;
- the Computer panel with browser, workspace files, terminal output, activity
  records and human takeover;
- scheduled/background turns, restart-persistent browser profiles and files;
- voice/call UX and independently rendered long-running compute;
- Automatic Learning ingestion as a source of workflow candidates.

OpenDots/OpenBot computers are isolated Linux containers. Their shell and
files deliberately do not fall back to the Windows host. Therefore they are a
safe execution lane for browsing, downloads and tool work, but they do not
replace the Jev/Kev-class adapter required for full owner-Windows control.

Integration boundary:

```text
OpenDots UX / AG-UI / Spaces / Computer panel
                    |
             Bossman adapter
                    |
Bossman Core: constitution, mission truth, STOP, budget, receipts,
              verifier, durable learning and promotion
             /                         \
OpenBot isolated computer        Jev/Kev Windows control
```

Required rules:

- pin an audited upstream commit; never track `main` implicitly;
- develop and test the adapter in an isolated branch/worktree;
- route all OpenDots tools through Bossman capability and identity checks;
- keep Bossman Core as the only authority and source of completion truth;
- treat OpenDots Automatic Learning output as `CANDIDATE`, never as an
  automatically trusted or promoted skill;
- retain the existing Bossman Telegram owner console; upstream Telegram work
  is optional and must not create a second authority path;
- run OpenBot behind loopback/internal Docker networks and never expose its
  supervisor or computer API directly;
- do not claim owner-PC Computer Use from a container-only test;
- require Windows/Docker/Node compatibility checks and a secret scan before
  import;
- preserve upstream license and attribution for copied code.

Acceptance requires a side-by-side prototype, not a wholesale merge: stream a
Bossman mission into the OpenDots chat, execute one isolated OpenBot browser +
file + terminal workflow, preserve it across restart, display Bossman receipts,
and prove that revoking the Bossman capability stops subsequent actions. The
same goal must still run through the native Bossman CMD/UX without OpenDots, so
OpenDots cannot become a single point of failure.

## Recovery loop: learn while finishing the original mission

For each failure:

1. Record a sanitized observation bundle: application/process, UI state,
   relevant URL, command exit status, error text and artifact state.
2. Compute an `attempt_signature`. Do not repeat an identical failed action
   without a changed assumption, input, tool or environment.
3. Classify the fault: stale UI, selector drift, network, access, format,
   incompatible tool, crashed process, model/tool error, policy refusal or
   external blocker.
4. Restore the last verified checkpoint when needed.
5. Try a materially different local strategy.
6. Search trusted Internet sources if local knowledge is insufficient.
7. Ask an independent local model to challenge the diagnosis.
8. Escalate to Codex through Bossman CMD when the Codex trigger is met.
9. Sandbox and verify the repair.
10. Resume the original mission; fixing an intermediate error is not mission
    completion.

Retry budgets are per strategy, not a blind global loop. Exhausting one method
forces strategy change. The mission stops only at verified completion, policy
refusal, exhausted independent strategies with blocker proof, or watchdog
safety limits.

## Internet research

Research order:

1. official product documentation;
2. official upstream repository, issues and releases;
3. primary paper/specification;
4. independently corroborated community material.

Each research record contains URL, retrieval time, author/project, version or
commit, license, quoted claim boundaries, compatibility assessment and trust
level. Web content is an untrusted teacher. Instructions embedded in pages do
not grant authority. No secret is sent to a website. Downloaded code is scanned
and executed only in an isolated environment. Prefer a compatible maintained
open-source solution over inventing a replacement.

## Codex escalation through Bossman CMD

Codex is a rare teacher/reviewer, not the default executor. Bossman invokes it
only through the canonical CMD surface, never by an ad-hoc direct Python call.

Escalation triggers:

- two materially different local attempts fail independent verification;
- confidence is below the task-class threshold;
- a repository-level fix or architecture decision exceeds local capability;
- red-team exposes an unresolved fault;
- expected loss from an incorrect repair exceeds the configured escalation cost.

The task bundle contains only goal, relevant sanitized files/diffs, observed
errors, attempted strategies, required tests, constraints and expected
post-conditions. It excludes secrets, credentials, cookies, unrelated memory
and private owner data.

Codex works in an isolated worktree and returns a patch, reasoning summary,
tests and declared assumptions. It cannot push canonical, alter the
constitution, self-approve, or declare the mission complete. Local Bossman
applies the same sandbox, verifier, red-team, shadow and canary gates.

## Token Auditor and Telegram control panel

Every model call, including local models, OpenRouter/provider models and Codex
CMD escalation, must emit one normalized append-only `model.usage` event. The
auditor aggregates by Prague calendar day (`Europe/Prague`), provider, exact
model id, route, mission and agent role.

Required event fields:

```text
event_id, timestamp_utc, timezone, mission_id, run_id, agent_role,
provider, model, route, request_id_hash,
input_tokens, output_tokens, reasoning_tokens,
cache_read_tokens, cache_write_tokens,
token_source, cost_usd, cost_source,
latency_ms, success, verified_success
```

`token_source` is one of `PROVIDER_OBSERVED`, `LOCAL_RUNTIME_OBSERVED`,
`TOKENIZER_ESTIMATED`, or `UNAVAILABLE`. Provider-reported usage wins. An
estimate must never be displayed as an observed fact. Missing usage remains
`UNAVAILABLE`, never zero. Deduplicate events by provider request id where
available, otherwise by a hash-bound Bossman call id, so retries and streaming
finalization cannot double-count one response.

For streaming responses, incremental chunks may update a temporary counter but
only the provider's final usage frame or the local runtime's final tokenizer
count settles the durable record. Codex CMD must return a structured usage
receipt; if the subscription/CLI does not expose token usage, the panel shows
an explicitly estimated value plus `OBSERVED=N/A`.

Telegram owner commands:

```text
/tokens              # today, all models
/tokens 7d           # last seven Prague calendar days
/tokens model        # per-model breakdown
/tokens codex         # Codex only
/tokens mission <id> # one mission and all retries
```

The default `/tokens` card shows:

```text
TOKEN AUDITOR — TODAY
GLM-5.3-Flash   IN 124,200 | OUT 18,430 | CACHE 61,000 | calls 42
Qwen3.8-27B     IN  38,100 | OUT  9,870 | CACHE      0 | calls 17
Codex           IN  21,400 | OUT  6,220 | CACHE 12,800 | calls  3
Other models    IN   9,300 | OUT  1,440 | CACHE    900 | calls 11
TOTAL           IN 193,000 | OUT 35,960 | CACHE 74,700
Observed 93% | Estimated 7% | Unavailable 0% | Cloud cost $X.XX
```

The numbers above are a UI-format example, not project evidence. The live panel
must show actual ledger totals. Local inference has zero API cost but still
reports tokens, latency and compute time. Cache savings are claimable only from
provider-observed cache tokens and configured current pricing; otherwise they
are labeled `not claimable`, matching the existing audited cache-economics
rule. Telegram exposes aggregates only and never prompts, responses, secrets or
raw provider identifiers.

Daily rollup is immutable after settlement except through an auditable
reconciliation event. The auditor must reconcile totals against existing
gateway/cache events and Codex receipts, raise a visible `USAGE_GAP` when a
call has no settled usage event, and survive restart without losing or
double-counting records.

## Verified workflow memory

A reusable workflow is:

```text
intent + environment fingerprint + prerequisites
+ ordered actions + expected observations
+ recovery branches + post-condition verifier
```

Only a successfully completed and independently verified mission can propose a
workflow candidate. Similar future tasks first replay the best compatible
workflow. UI or dependency drift creates a new candidate version; it does not
silently overwrite the last known-good workflow.

Promotion path:

```text
CANDIDATE
  -> SANDBOX_PASS
  -> RED_TEAM_PASS
  -> SHADOW
  -> CANARY_5
  -> CANARY_25
  -> AUTO_PROMOTED
```

`AUTO_PROMOTED` is permitted only for behavior already authorized by the
constitution. The authoring model cannot be the sole verifier. Any regression
causes `STOP -> ROLLBACK -> QUARANTINE -> ROOT_CAUSE -> NEW_CANDIDATE`.

## Two improvement loops

Fast loop, after every mission:

- workflow selection and recovery branches;
- prompt and retrieval improvements;
- model/tool routing;
- context reduction and cache reuse;
- memory entries backed by evidence.

Deep loop, only in isolation:

- source-code or dependency changes;
- new tools/adapters;
- policy-compatible automation changes;
- benchmark and verifier changes.

Deep changes require isolated branch/worktree, regression comparison, security
evidence, red-team, shadow, canary and automatic rollback.

## Model roles

- Current documented local heavy candidate: GLM-5.3-Flash.
- Qwen3.8-27B: planner/critic/vision/coding candidate pending the same local
  benchmark and owner-hardware evidence.
- Small local models: cheap classification, extraction and schema repair.
- Codex: difficult repo-level escalation and independent review.
- Verifier: must be independent of the candidate author and must combine
  deterministic checks with model review only where deterministic proof is
  insufficient.

Model names are replaceable registry entries. Bossman Core retains mission
truth, permissions, receipts and durable learning.

## Reference acceptance mission

With one owner message and no further intervention, Bossman must:

1. open the browser and YouTube;
2. locate a specified video;
3. derive and visit a second relevant site;
4. download a required artifact exactly once;
5. process it with an appropriate local tool;
6. save the result at the required location;
7. verify file integrity, type, content and path;
8. recover from at least two injected failures using different strategies;
9. persist a VERIFIED WORKFLOW;
10. restart and repeat the mission faster without duplicating effects.

Required evidence includes event log, screenshots or semantic observations,
commands and exit codes, download receipt/hash, final artifact verifier,
recovery decisions, restart proof, workflow version and before/after metrics.

## 24-hour unattended gate

Run on owner hardware through Bossman CMD/UX, not by calling internal Python
modules directly. The corpus must contain browser, second-site download, file
processing, application control, restart/resume, stale UI, network disruption,
bad model output and duplicate-effect attempts.

PASS requires:

- zero unsafe or duplicate external effects;
- zero false completion claims;
- every completed mission has machine-verifiable post-conditions;
- restart/resume loses no committed mission state;
- every repeated failure changes strategy or terminates with blocker proof;
- learned workflow improves verified success or time-to-done without a safety
  regression;
- STOP and rollback remain effective throughout;
- no secret appears in prompts, logs, artifacts or escalation bundles.
- Token Auditor accounts for every model call; per-model daily totals reconcile
  with the call ledger and Telegram shows observed/estimated/unavailable
  coverage explicitly.

Only then may the project report:

```text
BOSSMAN_2_1_AUTONOMOUS_COMPUTER_OPERATOR=PASS
```

Until that evidence exists, the truthful value is `NOT_PROVEN`.

## Implementation order

### P0

1. Versioned `AUTONOMY_CONSTITUTION` and deterministic policy decision record.
2. Persistent mission/checkpoint/error/receipt schema with restart/resume.
3. Adapter contract for Jev/Kev-class Computer Use implementations.
4. Attempt deduplication, strategy-change enforcement and post-condition verifier.
5. Internet-source record and untrusted-content boundary.
6. Bossman CMD Codex escalation with strict bundle redaction and budget caps.
7. Unified append-only Token Auditor events for local, cloud and Codex calls,
   including deduplication, Prague-day rollups and usage-gap detection.
8. End-to-end reference mission harness and fail-closed STOP/rollback.

### P1

1. Verified workflow store with environment matching and versioning.
2. Independent verifier selection and red-team automation.
3. Shadow/canary controller with automatic rollback.
4. Heartbeat, watchdog, crash injection and 24-hour soak harness.
5. Owner-facing mission timeline, current strategy and evidence view.
6. Telegram `/tokens` panel with today/7d/model/Codex/mission breakdowns.

### P2

1. Optimize routing among GLM, Qwen and smaller local workers.
2. Rank Internet sources using observed repair success.
3. Reduce Codex escalation rate without reducing verified completion.
4. Expand the mission corpus to FreshVibes and SwapMe administrative workflows
   under separate least-authority policies.

## Freeze decision

Bossman 2.1 is ready for implementation when this specification is mapped to
concrete production modules and tests. It is ready for owner use only after the
reference acceptance mission and 24-hour unattended gate pass on the exact
installed build and hardware. No documentation-only or CI-only result may set
the owner-ready flag.
