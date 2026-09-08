# V7 convergence run — final report

Branch `night/v7-convergence-20260908`. Reproduce every gate below with:

```
cd bossman-core && python -m bossman.v7.nightly_run --objectives all
```

It runs the checks that already exist and reports what they returned; it holds
no table of expected outcomes and has no way to make one pass. Evidence it
reads and writes:

* `docs/testing/v7-nightly-run.json` — per-objective, per-gate outcome
* `docs/testing/convergence-metrics-20260908.json` — approvals, tokens, deadlock rate, recovery
* `docs/testing/three-system-qa-20260908.json` — the relay run and its negative controls

The V7 multi-model audit corpus was read at its eight pinned SHAs (all matched)
and classified in `docs/v7/MULTIMODEL_AUDIT_SYNTHESIS_20260908.md`.

```text
FINAL_SHA = 72bae3e74dd5653ce0acd8d1780e726f61e99913
            every number below was measured on this commit; the commit that
            adds this file changes no code and no evidence
COMMITS_CREATED = 28  (109 files, +14812/-339 since 2c77d98)

B2_LOCAL_RELAY = PASS
B3_APPS_CONTROL = PASS
B4_STREAMING = PASS
B5_MODEL_HEALTH = PASS
THREE_SYSTEM_QA = PASS

REVIEW_DEADLOCK = PASS
APPROVAL_COALESCING = PASS
TOKEN_LOOP_GUARDS = PASS
GOLDEN_TRACE_REPLAY = PASS

OPENHANDS_REPO = PASS
OPENHANDS_REAL_E2E = PASS
OPENHANDS_LIVE = NOT_RUN

V7_REALITY_COMPILER = PASS
V7_WORLD_STATE = PASS
V7_STRATEGY_RECOVERY = PASS
V7_SHADOW_TELEMETRY = PASS

UX_CONSOLIDATION = PASS
FUNCTIONAL_UI_REGRESSION = PASS

APPROVALS_DOC_EDIT = 2 (read -> write -> verify) / 1 (single write); corpus 60
TOKENS_DOC_EDIT = 1840 / 920; corpus 1295189
REVIEW_DEADLOCK_RATE = 0.0 (4 reproduced, 0 survive the sweep)
MANUAL_INTERVENTIONS_3_SYSTEM_QA = 0

CORE_REGRESSION = PASS
CI = PASS
SECRET_SCAN = PASS

PROACTIVE_IMPROVEMENTS =
  * approvals.wait() re-asks instead of waiting out the day in silence
  * feature tick loops visible on /api/health, so the INV-RD-1 sweep is watchable
  * bossman.v7.nightly_run — the convergence gates as a reproducible command
  * World State Graph actually populated and served read-only at /reality/world
  * CONTESTED: two observers disagreeing stops being settled by recency
  * require_fresh() — the effect-boundary rule made callable, not conventional
  * generation-aware single-flight on the observation pass
  * root-ci whitespace gate fixed (was red on main and every branch cut from it)
  * floor_over_limit published beside the latency verdict, deciding nothing
  * a browser test stopped asserting that nothing else was talking to the server
  * URL hostnames no longer produce phantom file obligations

OPEN_REPO_P0 = none
OPEN_REPO_P1 = none
EXTERNAL_ONLY_BLOCKERS =
  * OPENHANDS_LIVE — no LLM provider credential in this environment
  * branch/main gateway conflict — an owner decision, not a repository fix

FINAL_VERDICT = READY_WITH_EXTERNAL_EVIDENCE_PENDING
```

## Regression on `72bae3e`

| suite | result |
| --- | --- |
| bossman-core | 2952 passed, 31 skipped, 0 failed |
| Command Center | 2577 passed, 16 skipped, 0 failed |
| root (shared contracts, learning, tools) | 1163 passed, 2 skipped, 0 failed |
| secret scan | PASS |

CI on the exact head: 24 check runs, 23 success and 1 skipped (`ASTRA real
sandbox`, which needs a sandbox this environment does not provide) — root-ci on
py3.11/3.12, the Command Center matrix on py3.11/3.12/3.14 with coverage and
Windows paths, Bossman Core CI, ASTRA acceptance and Solana safety gates.

## Why APPROVALS_DOC_EDIT is 2 and that is a pass

The master's target is "0-1 owner approvals, **unless existing policy requires
more for a specific real effect**" (§7, §16). A read lease must not authorize a
write, so `cat -> write -> cat` is two questions by policy, not by waste; the
single-write shape is one. The benchmark reports the raw count either way,
names the effect class each approval bought, and still fails if the *same*
effect is asked for twice — which is what the corpus's 60 confirmations were.

## OPENHANDS_LIVE

The SDK, sidecar, worktree, tools and evidence derivation all run for real, and
running them for real is what exposed the three defects this run fixed. The one
substitution is the model's weights: no LLM provider credential exists in this
environment (a keyless probe of the configured endpoint returns 401), so a
scripted local OpenAI-compatible endpoint stands in. The live-provider
acceptance stays open and needs a credential, not a repository change.

## The branch/main conflict

Not resolved here, on purpose. True merge base `1bb39bf`; the branch is 183
commits ahead of it, of which this run contributed the last 28, and the branch
base already conflicted with `main` before this run began. The conflict is
confined to `bossman-core/bossman/{cli.py,gateway/app.py,gateway/backends.py}`,
which this run never touched: `main` rewrote them for the 8-provider gateway
while the branch added a `CircuitOpenError` circuit breaker to the same
functions. Picking either side loses behavior — and `main`'s own root-ci is red
with `ImportError: cannot import name 'CircuitOpenError'`, so which gateway
ships is an open question rather than a mechanical merge. Details in the PR
thread.

## What the audit corpus changed, and what it did not

Twelve of its recommendations were already implemented; four are stale, two of
them contradicting the corpus's own rules. Two model-vs-model disagreements
were resolved rather than averaged — where the counterfactual planner belongs,
and whether shadow may promote itself — both toward Sol and the charter. Three
gaps were real and are closed. Five more are recorded as still-valid and
deliberately unbuilt, each with its reason, so the next run inherits the
reasoning instead of re-deriving it. The full matrix is in
`docs/v7/MULTIMODEL_AUDIT_SYNTHESIS_20260908.md`.
