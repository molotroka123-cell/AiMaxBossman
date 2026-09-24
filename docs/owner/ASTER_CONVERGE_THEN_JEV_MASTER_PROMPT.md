# ASTER — CONVERGE FIRST, THEN TEST JEV

Repository: `molotroka123-cell/AiMaxBossman`

You are the owner-run integrator. Execute, do not create a new planning-only branch.

## Mission

Produce one owner-testable branch where the newest Bossman 1.0 Exact fixes and the Twitch/Jev/local-vision collector coexist, then test Jev and the market pipeline on the owner PC.

The order is mandatory.

---

## PHASE 0 — CONVERGENCE FIRST

1. `git fetch --all --prune`
2. Checkout the existing target branch:
   `feat/jev-twitch-collector-20260924`
3. Confirm this branch contains collector commit:
   `2a74c63be9df0c85758bd82be741e38742836d48`
4. Confirm Exact branch:
   `claude/bossman-1-0-exact-20260924`
   at or descended from:
   `c489cc62398b7213efbc820f10b64e57f4f054e5`
5. Merge Exact **into the existing Twitch branch** with normal history preservation.
6. No force-push. No rebase that rewrites published history. No second "final" branch.
7. Resolve conflicts by preserving:
   - Exact privacy/red-team/Windows-100 fixes;
   - all `bcc.market.*` collector code;
   - Jev code;
   - Telegram companion code;
   - trading casebook and safety gates.
8. Push the merged branch.
9. Record:
   - PRE_MERGE_EXACT_SHA
   - PRE_MERGE_MARKET_SHA
   - MERGED_SHA
   - merge-base
   - changed/conflicted files

### Mandatory convergence proof

Before any Jev test, prove on `MERGED_SHA`:

- `git merge-base --is-ancestor c489cc62398b7213efbc820f10b64e57f4f054e5 HEAD`
- `git merge-base --is-ancestor 2a74c63be9df0c85758bd82be741e38742836d48 HEAD`

Both must exit 0.

If either fails: **STOP. NO JEV TEST.**

---

## PHASE 1 — BASELINE TESTS ON MERGED SHA

Run the smallest relevant suites first:

- market collector tests;
- Jev unit/browser tests;
- Telegram companion Jev tests;
- trading learning / Coinwise observation tests;
- security/privacy tests touched by the Exact merge.

Then run the normal owner/release gates appropriate to this branch.

Do not call a skipped test PASS. Report PASS / FAIL / SKIP separately.

### Critical audit before live run

Fix or quarantine these issues if still present:

1. The live market-analysis source of truth must not rely on the old coarse Coinwise classifier with `FLAT_CVD_RATIO=0.15`.
2. Canonical live interpretation should use the audited Trader Apprentice semantics / rule set / case evidence.
3. Restore last frame identity / last verified observation across collector restart if not already durable.
4. Never carry stale CVD/OI/price forward.
5. Never let the explanatory LLM invent a missing market number.

Only after baseline tests are green enough for an owner run may PHASE 2 start.

---

## PHASE 2 — JEV TEST

### 2A. Jev selftest

Run the local/mock Jev selftest first.

Expected:
- no browser side effect outside the test;
- schema validation works;
- timeout/failure falls back;
- STOP / approval rules remain authoritative.

### 2B. Jev live shadow

Only after selftest passes:

- owner supplies the Jev/TypeSafe key locally;
- key never enters Git/logs;
- perform the one cheap contract probe;
- then the existing shadow scenario suite.

Jev remains **non-authoritative**.

Jev may:
- inspect/support DOM/player state;
- choose/rank allowed routes;
- recommend escalation.

Jev may NOT:
- read a value drawn only in video/canvas pixels;
- invent CVD/OI/price;
- bypass approvals;
- approve itself;
- place trades/orders;
- become final verifier.

For Twitch pixel values the required decision is:
`video/canvas → escalate_local_vision`.

Record latency, fallback, agreement and contract status.

If contract probe fails: Jev status = BLOCKED/PARTIAL, but the local market collector should still be testable without Jev.

---

## PHASE 3 — LOCAL VISION + TWITCH CALIBRATION

Use the current collector.

First run calibration with saved frames/crops.

Acceptance:
- fresh-frame proof works;
- symbol/instrument binding is correct;
- readable CVD/OI values have **zero tolerated accepted numeric errors** in reviewed calibration;
- disagreement becomes LOW_CONFIDENCE/UNREADABLE;
- stale frame has null values;
- cloud vision is not used.

Known local-vision rule:
three decorrelated 8x reads must agree before a value is VERIFIED.

Do not silently downgrade quality requirements just to improve coverage.

---

## PHASE 4 — LIVE DATA RUN

Run 60 minutes at the configured live cadence.

Verify:
- Twitch/player recovery;
- quality pin/re-pin;
- STOP responsiveness;
- restart behavior;
- JSONL truth ledger;
- SQLite WAL index;
- no stale backfill;
- no duplicate semantic observations;
- extractor version recorded.

Generate an end-of-run report from the ledger, not from handwritten counts.

---

## PHASE 5 — MARKET ANALYSIS

Wire the latest compatible VERIFIED observation to the previous compatible VERIFIED observation.

Required deterministic output:

- timestamp
- instrument/source
- price
- CVD
- OI
- exact deltas
- Price/CVD/OI directions
- regime
- structural levels when available
- missing-data list
- classification confidence
- matching historical CASE references

Use:
- `docs/trading/BTC_ORDERFLOW_PLAYBOOK.md`
- `data/trading/btc_orderflow_rules_v1.json`
- Trader Apprentice semantics
- canonical case registry/case files

Do not output invented trade-win probabilities.

---

## PHASE 6 — LOCAL LLM

The local model explains only typed/deterministic evidence.

It may simplify language and compare with historical CASE patterns.

It may not alter raw numbers or upgrade UNKNOWN to a number.

If the deterministic analyzer says data is incomplete, the text must say so.

---

## PHASE 7 — TELEGRAM

Reuse the existing owner-only Telegram companion transport.

Default notification policy:
- regime change;
- important reclaim/loss;
- material CVD/OI anomaly;
- data-quality failure/recovery;
- optional periodic digest.

Do not send an identical analysis every 15 seconds.

Keep a notification fingerprint/cooldown.

Telegram is output/notification only; it does not create exchange orders.

---

## PHASE 8 — REAL OWNER E2E PROOF

PASS requires a real owner-PC chain:

1. Twitch is live.
2. A fresh frame is captured.
3. CVD/OI/price are extracted.
4. Evidence/crops are stored.
5. Deterministic regime is produced.
6. Local LLM produces the short Russian explanation.
7. Telegram sends it to the owner.
8. Delivery result is recorded.

Save:
- MERGED_SHA
- timestamps
- raw observation
- frame/crop hashes
- extractor identity
- deterministic analysis JSON
- local model id
- Jev status/latency/fallback
- Telegram delivery result

Final verdict exactly one:
- PASS
- PARTIAL
- BLOCKED

Never say PASS from unit tests alone.
