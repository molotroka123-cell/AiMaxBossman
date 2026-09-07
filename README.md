# AiMaxBossman

**Local AI operating system / agent control plane for Windows and local-first workflows.**

> **Current checkpoint — 7 September 2026**  
> Active engineering line: `v6/velocity-phase0-baseline-20260907`  
> Last code/test candidate: `413a97a1ce2936f9543fea5d8f0b529256fc1de2`  
> V6 verdict: **`REPO_COMPLETE_EXTERNAL_VALIDATION_PENDING`**  
> Known repository-fixable P0: **0** · known repository-fixable P1: **0**, subject to final uninterrupted exact-SHA CI and owner re-test.

Bossman is built to turn an owner goal into a controlled, observable execution: plan the work, route it to local/cloud models, use tools, request approval where required, verify the post-state, preserve recovery evidence and refuse to report success when the real effect is not proven.

## State today

V4/V5 safety and execution-truth work is carried into the V6 line. The current work is no longer “implement the missing core”; it is **finish certification and prove the product on the owner’s real Windows/local-model environment**.

Repository evidence currently says:

| Area | Current state |
|---|---|
| Effect / completion truth | **VERIFIED repo-local** — AT-01/AT-03 regressions, post-state verification, fencing and anti-replay remain enforced |
| V6 performance phase 0/1 | **Implemented** — startup tracing, lazy pages, app-probe coalescing/cache, Computer Use phase timing, FFmpeg background priority |
| Bossman Core CI on `413a97a1ce29` | **Green** across coverage, security, gateway/stage/rest jobs |
| ASTRA / Solana safety | **Green** on the code candidate |
| Windows-path CI | **Green**; this is **not** owner Windows desktop acceptance |
| Command Center 3.11 / 3.12 / 3.14 | 3.14 is a hard lane; the last full exact-SHA matrix was interrupted by later documentation pushes, so final exact-SHA certification remains **UNPROVEN** |
| Owner Windows + configured local model | **NOT_RUN / external validation pending** |
| Same-model intelligence retention | **INSUFFICIENT_EVIDENCE** — no valid current artifact |
| Real Video Studio / Web Designer owner flow | **needs second owner acceptance run** |

Canonical handoff: [`docs/v6/V6_FREEZE_REPORT.md`](docs/v6/V6_FREEZE_REPORT.md).  
Execution prompt for the next Fable pass: [`docs/v6/FABLE5_MASTER_PROMPT.md`](docs/v6/FABLE5_MASTER_PROMPT.md).

## What changed in V6

V6 Phase 0/1 focuses on **measured owner-perceived speed without weakening safety boundaries**.

Measured same-scenario results from the V6 pass:

| Metric | Before | After |
|---|---:|---:|
| JS modules on first-render critical path | 42 / 788 KiB | **14 / 290 KiB** |
| `ui_ready` primary sample set | 401 / 411 / 409 ms | **287 / 244 / 281 ms** |
| `first_page_rendered` p50 | ~600 ms | **~460 ms** |
| launcher-probe event-loop stall (9 manifests) | ~202 ms | **~18 ms cold / ~5 ms warm** |
| `apps.collect()` cold | ~245 ms | **~113 ms** |
| Home burst during old blocked loop | ~290–320 ms/request | **~89–147 ms/request** |
| idle process-tree RSS in recorded sandbox sample | — | ~127.2 MB HWM |

These numbers do **not** claim GPU/local-model speed. Owner-host `FIRST_USEFUL_RESPONSE`, `VERIFIED_ACTION`, model residency/reload behavior and interactive p95 under sustained real video export are still external measurements.

### V6 implementation highlights

- **Lazy Command Center pages** — heavy feature modules load on demand instead of blocking the initial dashboard.
- **StartupTrace** — `/api/system.startup` exposes startup phases; missing measurements remain `null`, never fake zeroes.
- **App discovery single-flight** — concurrent UI callers share one side-effect-free probe instead of multiplying work.
- **Manifest/probe reuse** — safe immutable metadata is cached; launcher probes reuse the HTTP client/SSL context.
- **Computer Use timing** — observe/plan/act and surrounding phases are measurable without caching across freshness boundaries.
- **Background media QoS** — FFmpeg children run below normal interactive priority.
- **Python 3.14 CI** — promoted to a hard Command Center lane after the owner session exposed a strong 3.14 correlation and a prior green run justified making it mandatory.
- **Truthful performance harness** — `tools/v6_baseline.py` binds evidence to exact SHA/tree and reports unavailable GPU/model measurements as `NOT_RUN`.

## First owner session: what was fixed

The first real testing-period session (`6cbb17ce84db`) was used as product evidence, not as a synthetic benchmark.

Confirmed/fixed repository issues include:

- reconnect control no longer fails silently;
- a mission whose tasks are all blocked no longer stays “running” forever;
- app-launch ownership survives Command Center restart conservatively instead of returning the old 409 loop;
- Video Studio reports concrete unavailability reasons instead of flattening them;
- OpenRouter/provider wizard paths received targeted fixes;
- Web Designer / Video provider failure paths received targeted fixes;
- Trading Lab no longer crashes when the old core module is absent; an unwired build reports `DEAD_OR_UNWIRED`;
- dashboard startup/interaction was materially improved by lazy pages and app-probe single-flight.

The old raw “dead click” count is **not** the bug count. Corrected telemetry showed false positives when visible feedback happened in modals, toasts, focus state, ancestors or controls outside the detector’s old observation surface.

## What still must be proven on the owner machine

The next acceptance run should keep one Dashboard session alive across 4–5 sequential tasks and explicitly cover:

1. **Windows desktop launch** — real visible owner-session window, not PID/port-only evidence.
2. **Configured local model/provider** — actual route/model, latency, retry/fallback reason and privacy behavior.
3. **Video Studio** — import → thumbnail/waveform → play → two edits → export → decode/probe → close/reopen persistence.
4. **Web Designer** — real AI edit, persisted result, and explicit failure instead of silent success when provider calls fail.
5. **Computer Use** — safe multi-step mission with owner approvals and post-state verification.
6. **Long-session stability** — memory growth, duplicate subscribers, zombie runs, stuck approvals, stale context and queue starvation.
7. **Windows-only media/read verification debt** — `CC-VIDEO-READVERIFICATION-WINFILE` only if it reproduces on the current code.
8. **Same-model intelligence retention** — only a genuine measurement artifact can close this; documentation cannot substitute for it.

A Windows GitHub runner is useful evidence but **not** owner Windows acceptance. A fake adapter is **not** local-model acceptance. A unit-test Video export is **not** a real owner Video Studio workflow.

## Architecture

```text
AiMaxBossman/
├── bossman-core/
│   ├── bossman/                 gateway, runner, approvals, operator, learning, factories
│   └── bossman_v3/              durable execution, memory, Organization, Fleet
├── command-center/
│   ├── bcc/                     FastAPI control plane, tasks, tools, providers, approvals
│   └── ui/                      owner dashboard and feature pages
├── bossman_shared/              shared evidence / action / cache contracts
├── apps/                        launchable applications and manifests
├── learning/                    learning traces and training artifacts
├── bossman-infra/               local infrastructure definitions
├── docs/                        audits, freeze reports, acceptance evidence
└── tools/                       CI, security, benchmark and V6 measurement utilities
```

Core execution model:

```text
OWNER INTENT
    ↓
PLAN / MISSION
    ↓
MODEL + TOOL ROUTING
    ↓
POLICY / BUDGET / PRIVACY / APPROVAL
    ↓
EXECUTION
    ↓
FRESH POST-STATE VERIFICATION
    ↓
EVIDENCE / JOURNAL / RECOVERY
    ↓
VERIFIED RESULT
```

For effectful work, model prose is never sufficient evidence of success.

## Major capabilities

- local + cloud model routing with explicit privacy/cost policy;
- task and mission orchestration;
- browser, terminal, files, MCP and desktop/computer-use paths;
- owner approvals (`AUTO / ASK / DENY`) and fail-closed authorization;
- durable task/run state, checkpoints, recovery and fencing;
- Organization + Fleet orchestration layers;
- scoped memory/context and learning traces;
- skill recording/shadow/promotion behind feature gates;
- Video Studio and Web Designer application workflows;
- Cost Governor / hard budget boundaries;
- secret scanning, SAST/SCA, SSRF/path/symlink containment and audit logging;
- testing-period telemetry for real owner sessions.

Dangerous/autonomous features remain opt-in. Performance work must not bypass approvals, freshness, authorization, budgets, fencing, recovery or post-state verification.

## Quick start

### Command Center

```bash
cd command-center
python -m venv .venv
# Windows:
.venv\Scripts\Activate.ps1
# Linux/macOS:
# source .venv/bin/activate

pip install -e ".[dev]"
bcc
```

Default local UI: `http://127.0.0.1:8800`.

### Bossman Core

```bash
cd bossman-core
python -m venv .venv
# activate the venv
pip install -e ".[dev]"
bossman serve
```

Typical CLI:

```bash
bossman task "..."
bossman project plan <slug> <brief.md>
bossman project run <slug>
```

Provider credentials belong in environment/Vault configuration, never in committed files.

## Verification

Useful repository checks:

```bash
python tools/ci_secret_scan.py
python tools/skips_registry.py --check
python scripts/update_readme_scorecard.py --check

cd bossman-core
python -m pytest -q --timeout=120

cd ../command-center
python -m pytest -q --timeout=120
```

CI truth rules:

- `SKIP != PASS`
- `NOT_RUN != PASS`
- cancelled by a newer push `!= PASS`
- green on another SHA `!= green on this SHA`
- owner Windows acceptance `!= windows-latest CI`
- model prose `!= post-state evidence`

## Current evidence map

| Need | Source |
|---|---|
| V6 source truth / remaining acceptance | [`docs/v6/V6_FREEZE_REPORT.md`](docs/v6/V6_FREEZE_REPORT.md) |
| Fable completion instructions | [`docs/v6/FABLE5_MASTER_PROMPT.md`](docs/v6/FABLE5_MASTER_PROMPT.md) |
| V6 measurement contract | [`docs/v6/METRICS_BASELINE_AND_BENCHMARK.md`](docs/v6/METRICS_BASELINE_AND_BENCHMARK.md) |
| Multi-model audit synthesis | [`docs/v6/MULTI_MODEL_AUDIT_SYNTHESIS.md`](docs/v6/MULTI_MODEL_AUDIT_SYNTHESIS.md) |
| Owner-session findings | [`docs/testing/acceptance-run-20260906/OPEN_FINDINGS.json`](docs/testing/acceptance-run-20260906/OPEN_FINDINGS.json) |
| Owner app-launch acceptance contract | [`docs/testing/OWNER_SESSION_APP_LAUNCH_ADDENDUM_20260906.md`](docs/testing/OWNER_SESSION_APP_LAUNCH_ADDENDUM_20260906.md) |
| V4/V5 release evidence | [`docs/v5/V5_RELEASE_SCORECARD.md`](docs/v5/V5_RELEASE_SCORECARD.md) |
| Machine maturity scorecard | [`docs/benchmark/current-scorecard.json`](docs/benchmark/current-scorecard.json) |

## Epoch status

| Epoch | Current meaning |
|---|---|
| 1–3 | local tools, orchestration, durable execution/memory/Fleet foundations |
| 4 | Continuity / verified mission state and effect truth — core safety work carried into current line |
| 5 | Steward / controlled promotion, canary/rollback, sustained objectives — repo-local foundations carried forward; owner/live acceptance remains separate |
| 6 | **Velocity** — measured latency/resource/UX improvements without reducing correctness; Phase 0/1 is the current active release line |

## Live OS Scorecard

This block is generated from `docs/benchmark/current-scorecard.json`.  
Do not hand-edit the table; update the JSON and run `python scripts/update_readme_scorecard.py`.

<!-- BOSSMAN_LIVE_SCORECARD_START -->
| # | Ось системы | Оценка | Статус | Уверенность | Улики |
|---|---|---:|---|---|---|
| 1 | Execution Truth | 8.8/10 | VERIFIED | HIGH | AT-01 effect obligations and fresh post-state verification are closed with regression coverage; Fencing, anti-replay and recovery invariants remain in force; V6 performance work did not weaken effect-boundary semantics |
| 2 | Security | 8.5/10 | VERIFIED | HIGH | Secret scan and blocking SAST/SCA completed successfully on the V6 code candidate; Approvals, authorization, privacy routing, freshness and fail-closed behavior were explicitly preserved through V6 |
| 3 | Tooling / OS Integration | 7.5/10 | INTEGRATED | MEDIUM | Windows-path CI is green on the current V6 code candidate; Owner-session reconnect, app restart recovery, provider UX and Trading Lab crash paths have repository fixes |
| 4 | Organization Layer | 7.3/10 | INTEGRATED | MEDIUM | Mission/task orchestration remains durable and blocked-only missions now terminate honestly instead of appearing to run forever; Organization/Fleet execution contracts and verified-child completion rules remain covered |
| 5 | Fleet & Resources | 7.0/10 | INTEGRATED | MEDIUM | Lease/fence/queue safety contracts remain covered and V6 does not bypass the canonical execution path; Interactive work is protected from background FFmpeg contention by lowered child-process priority |
| 6 | Memory / Context | 6.5/10 | IMPLEMENTED | MEDIUM | Durable task/recovery memory and scoped context contracts remain intact; Long-session testing now explicitly watches stale context, zombie runs, duplicate subscribers and memory growth |
| 7 | Testing / CI | 8.0/10 | VERIFIED | MEDIUM | Bossman Core full coverage/rest/security/gateway/stage8-14 jobs are green on 413a97a1; ASTRA acceptance, Solana safety, Windows paths and secret/SAST gates are green; Python 3.14 is a hard Command Center lane |
| 8 | Observability / CEO Control | 7.0/10 | PARTIAL | MEDIUM | V6 adds Services.start phase tracing, UI_READY/first-page timing and Computer Use phase_timing; Testing-period evidence from owner session 6cbb17ce84db is retained with corrected dead-click classification |
| 9 | Treasury / Cost | 6.8/10 | IMPLEMENTED | MEDIUM | Budget/cost gates remain unchanged and fail-closed during V6 performance work; Unknown pricing is not treated as free and Fable hard-cap accounting remains isolated from tests |
| 10 | Mission UX / Command Center | 6.8/10 | IMPLEMENTED | MEDIUM | V6 lazy pages reduced first-render modules from 42/788 KiB to 14/290 KiB in the measured harness; Owner-session reconnect, blocked mission, app restart and provider/trading error paths received targeted fixes |

- **Current bottleneck:** V6 phase 0/1 is repository-complete pending external validation: Core/ASTRA/Solana/Windows/security evidence is green on the current code candidate, but an uninterrupted full Command Center exact-SHA matrix plus owner Windows/local-model/Video/Web Designer/long-session acceptance is still missing.
- **Next highest-value fix:** Finish one uninterrupted exact-SHA CI certification, then run the second owner Dashboard acceptance on Windows with the configured model/provider and close only reproduced Video Studio, Web Designer and long-session findings.
- **Last evidence SHA:** `413a97a1ce2936f9543fea5d8f0b529256fc1de2` · **Current HEAD SHA:** `see git rev-parse HEAD` · **Evidence freshness:** PARTIALLY_STALE_AFTER_DOC_COMMITS
- **Last scorecard update:** 2026-09-07
- **Benchmark hard failures:** none observed
- **Live hardware attestation:** PENDING
- **Exact-SHA CI:** UNPROVEN

_Среднее (вторично, не авторитетно): 7.4/10. 10.0 = ATTESTED; ни одна ось не ATTESTED без живой аттестации железа._
<!-- BOSSMAN_LIVE_SCORECARD_END -->

## For coding agents

Before changing architecture:

1. fetch the actual branch HEAD and current CI;
2. read `docs/v6/V6_FREEZE_REPORT.md` and `docs/v6/FABLE5_MASTER_PROMPT.md`;
3. inspect current `OPEN_FINDINGS` and owner-session evidence;
4. reproduce a finding on current HEAD before reopening it;
5. preserve already-closed AT-01 / AT-03 / CFR / containment work unless a new negative control fails;
6. write the regression with the fix;
7. run focused tests, then broad CI;
8. push to the active branch without force-push;
9. never invent Windows, GPU, local-model, provider or intelligence-retention evidence.

The next high-value milestone is **one clean exact-SHA repository certification followed by the second real owner Dashboard acceptance run**.
