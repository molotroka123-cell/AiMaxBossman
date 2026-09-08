# BOSSMAN — Claude Current-Work Audit (2026-09-08)

## Scope

Quick audit of what Claude has written since the current V7 convergence line, with emphasis on whether the new work creates risk for the planned Visual V3 UX mini update.

## Current convergence truth

PR #58 (`night/v7-convergence-20260908`) remains at:

`3d8814901dee343c442fb4cb34602064d73ce800`

No newer PR58 implementation commit was found during this check.

The PR still reports an unresolved semantic integration conflict with the newer provider gateway in exactly three files:

- `bossman-core/bossman/cli.py`
- `bossman-core/bossman/gateway/app.py`
- `bossman-core/bossman/gateway/backends.py`

That conflict is outside this UX lane. Visual work must not touch or mechanically resolve it.

## New parallel work on the default branch

The repository default branch `claude/bossman-control-v03-43igbk` has advanced separately through:

`da67a63251ab2ffb5d20a624772d2cb9a0427365`

The recent sequence adds a Trader Apprentice / deterministic BTC order-flow analysis capability, its corpus, tests and local-model orchestration documentation.

Positive properties observed:

- the engine is analysis-only and explicitly does not place orders;
- autonomous execution remains behind a separate permission/effect boundary;
- missing live values are intended to remain UNKNOWN;
- price/CVD/OI are intentionally analyzed together;
- weighted position math and order-flow matrix behavior have direct tests;
- the orchestration spec explicitly requires source/instrument/timestamp tagging and forbids treating incompatible CVD/OI series as identical.

## Material audit finding — TA-01 series compatibility is documented but not enforced

### Finding

The orchestration specification explicitly requires:

> never compare incompatible CVD/OI series as if they are identical

and the `Snapshot` type carries `source`, `instrument` and `timestamp` fields.

However, the deterministic `classify_regime(previous, current)` implementation currently derives Price/CVD/OI directions directly from the two snapshots without first proving that the CVD/OI observations belong to a compatible series/source/instrument.

The current dedicated tests cover matrix classification, weighted average entry, reclaim/acceptance and position math, but do not include a negative test where the previous and current snapshots come from incompatible instruments/providers/series.

### Impact

A caller can accidentally compare, for example, two different CVD feeds or an execution-market snapshot against a differently configured/reference series and still receive a deterministic-looking regime classification.

That would violate the documented invariant and can create false confidence precisely because the helper is deterministic.

### Recommended correction

Handle this in a **separate trading hardening commit**, not the Visual V3 branch.

Minimum acceptable design:

- define explicit compatibility metadata for each comparable series;
- fail closed or return `UNKNOWN` when comparable series identity is absent or mismatched;
- never infer compatibility from similar numeric magnitude;
- distinguish price source/instrument compatibility from CVD/OI series compatibility where appropriate;
- add negative tests for source mismatch, instrument mismatch, series/settings mismatch and missing identity;
- retain an explicit override only if it is typed, deliberate and auditable — never a silent fallback.

## CI/evidence note on the new Trader sequence

At observation time, the latest default-branch SHA had no combined commit statuses and no associated workflow runs returned through the repository connection.

Therefore the Trader Apprentice code should be treated as **new code requiring exact-SHA CI/acceptance**, not as already release-certified merely because targeted tests were committed.

## UX interaction risk

The new trading work does not need to be imported into Visual V3.

Visual V3 must not:

- add trading execution controls;
- surface `confidence` as probability of profit;
- turn Trader Apprentice stance into an automatic order action;
- invent CVD/OI live state for an animated avatar;
- combine the parallel default-branch trading history with PR58 through a broad merge.

If a Trader Apprentice agent/avatar is shown later, the UI should project honest statuses only and preserve `UNKNOWN` when data identity/freshness is insufficient.

## Existing closure items not reopened by this audit

This review found no new reason, from the Visual V3 work itself, to reopen:

- AT-01 effect-proof architecture;
- AT-03 freshness architecture;
- Video Studio CFR;
- N8/canary design;
- World State authority boundaries;
- B2/B3/B4/B5 convergence work.

The Visual V3 reference branch is deliberately based on PR58 HEAD and initially contains documentation/reference assets only. Runtime semantic delta at creation: **zero**.

## UX recommendation

Proceed with Visual V3 only as a performance-gated, reversible presentation lane.

Before implementation:

1. capture a frozen performance baseline;
2. keep Normal Mode default;
3. add static visual cleanup first;
4. add honest agent-state mapping second;
5. enable micro-motion behind a default-OFF flag;
6. build Living Agent Room only if the measured performance gate remains green.

Do not mix the Trader hardening or gateway integration into the same UX commits.