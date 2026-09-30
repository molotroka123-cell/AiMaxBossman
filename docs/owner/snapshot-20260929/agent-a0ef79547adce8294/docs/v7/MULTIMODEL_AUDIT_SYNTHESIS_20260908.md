# V7 multi-model audit corpus — synthesis against the night branch

Input: the eight pinned branches in `docs/night/V7_MULTIMODEL_AUDIT_REFERENCES_20260908.md`,
read at their pinned SHAs. Every one matched.

This file is a record of what was decided, not a substitute for the deciding.
Two implementations came out of it and are named below; nothing here is a
recommendation waiting for someone else to act on.

Corpus rule applied throughout: findings were revalidated against **current
night-branch code**, never against the SHA they were written at. Most of these
audits were written against V6 snapshots from 2026-09-07.

## ALREADY_FIXED — the corpus asks, the branch already does

| # | Recommendation | Sources | Where it lives now |
|---|---|---|---|
| 1 | Typed Mission IR, digest-bound, not a loose plan | Sol §1, charter §1, TZ Ph1 | `bossman_shared/mission_ir.py` |
| 2 | Reality Compiler: intent → validated IR, granting no authority | Sol §1, charter §1 | `bcc/reality/compiler.py`, `POST /reality/compile` |
| 3 | "Unknown state is not zero and not a default guess" | Sol (the 128 GB lesson), charter invariants | `Observation.available=False` + reason in every adapter |
| 4 | Utility terms kept separately, no fake decimal precision | Sol §3, audit-branch §4 | `bcc/reality/strategy.py` evidence bands |
| 5 | Recovery as bounded strategy change, not blind retry | Sol §7, charter §7, TZ Ph5 | `bcc/reality/recovery.py` ladder |
| 6 | Shadow telemetry: recommendation, chosen path, outcome, regret | Sol §V7.2, charter | `bcc/reality/telemetry.py`, `GET /reality/shadow` |
| 7 | Shadow stays non-authoritative | every source; non-negotiable rule | no execute/apply/promote route exists — asserted in tests |
| 8 | Routing informed by measured reliability, not static rules | Sol §4, Perplexity §1.6 | `bcc/model_health.py` + `models.health` |
| 9 | Retry budgets and loop detection | Sol §7 | `bcc/mission_budget.py` |
| 10 | Observation adapters for git/process/tasks/providers/apps | Sol §V7.1, TZ Ph1 | `bcc/reality/observers.py` |
| 11 | Effect attempted once across retry/recovery | TZ Ph5, audit-branch §13 | existing fencing + `approval_scope` |
| 12 | Uncertainty visible to the owner | Sol §10, charter §9 | evidence bands, `available:false` reasons, `/reality/world` |

## STALE — do not resurrect

* **`IMPLEMENTATION_TZ` freeze gate** ("STOP. DO NOT START V7 CODE YET"). Written
  when the V6 line was mid-acceptance. `CURRENT_AUDIT_2026-09-07` records no
  repository-fixable P0 or P1 at its own base, and the night master directs this
  work explicitly. The gate's *reasoning* survives as the evidence discipline
  the branch already follows.
* **`Strategy.p_success` as a float** (`v7/phase1-reality-core`). Superseded by
  evidence bands — which is the corpus's own rule ("do not expose fake
  precision", "do not use fake decimal probabilities if data is insufficient"),
  so the phase-1 shape contradicts the audits that specified it.
* **Perplexity's 128 GB / Ryzen-specific assumptions.** Sol and the TZ both
  forbid assuming that hardware is present; the V6 resource-admission fix exists
  precisely because invented memory state was a real defect.
* **Perplexity's validation targets** (world simulator >80% accuracy over 100
  missions, 1000-mission skill improvement, >90% auto-promotion accuracy). These
  are research programmes, not repository-fixable items.

## DISAGREEMENT — and how it was resolved

**1. Where the counterfactual planner belongs.**
Perplexity ranks it #2 and calls it "the single largest architectural gap".
Sol places it at wave V7.5, after world state, strategy and routing, and lists
"counterfactual simulation is mistaken for evidence" among the highest-risk
design failures. The charter agrees with Sol (§7, after the strategy engine).

*Resolved toward Sol/charter.* Prediction built over facts nobody has collected
is the wrong order, and the corpus's own synthesis rule says repository evidence
beats model opinion. The world state was the missing floor; it is now built.

**2. Automatic promotion when shadow beats production.**
Perplexity's step-change #3 wants auto-promotion. Sol §9 and the charter §6
require `candidate → replay → adversarial → benchmark → shadow → canary →
promotion` with an explicit gate, against the non-negotiable
`skill promotion != self-granted capability`.

*Resolved toward Sol/charter.* A safety concern is not averaged away — the
corpus's own synthesis rule 5. No auto-promotion was built.

## STILL_VALID — the gaps, and what was done about them

**A. The World State Graph had no runtime path.**
Sol's wave V7.1 is "populate from existing observations/events"; the charter §2
and audit-branch §3 say the same. Actual state: the storage contract existed
(`bossman_shared/objective_world_state.py`), the adapters existed
(`bcc/reality/observers.py`), `to_world_facts()` shaped facts for the one from
the other — and **nothing ingested them**. Every observation was computed for a
single HTTP response and discarded, so the graph was empty between requests,
freshness described nothing, and the charter's first UX question had no answer.

→ `bcc/reality/world.py`: one projection per process, refreshed on a 60s tick
(deliberately slower than the facts' own validity, so the graph can actually go
STALE), served read-only at `GET /reality/world`. A value appears only under a
FRESH status. There is no route that writes a fact, and a test asserts there
never is. `require_fresh()` makes the corpus's effect-boundary rule callable
rather than conventional.

**B. Two observers disagreeing was settled by recency.**
Sol's top-risk list opens with "World State Graph becomes a stale cache treated
as truth". `ingest` kept one fact per key, so a second source measuring
something different overwrote the first and the read stayed confident. The
phase-1 branch's `reconcile()` refuses to guess between conflicting sources;
the night branch's projection had no equivalent.

→ Facts are kept per source. While two fresh readings disagree the read is
`CONTESTED`, carries both, and `value_or_unknown()` returns UNKNOWN. Strictly a
tightening: a read that was FRESH can become CONTESTED; nothing that was UNKNOWN
becomes known. `True` and `1` count as disagreement — inventing agreement
between observers is the same failure as inventing freshness. Sources are capped
per key so visible disagreement cannot become an unbounded memory channel.

**C. Generation-aware single-flight** (charter §10) — named there as a specific
candidate for "same-generation observation".

→ Applied to the observation pass, which (A) had just made concurrent. The key
carries repo identity, a finished pass is never reused, cancellation is explicit,
and a failed pass is not remembered.

## STILL_VALID and open — deliberately not built tonight

Recorded so the next run does not have to re-derive them, with why each was left.

* **Attention / QoS scheduler** (Sol §8, charter §8, TZ Ph6). Real and valuable;
  a scheduler rewrite is not a change to make at the end of a convergence run,
  and its acceptance ("owner request preempts background export within bounded
  delay", "no starvation") needs its own measurement harness.
* **Dynamic mission teams** (Sol §5, TZ Ph4). Depends on the strategy layer
  being authoritative, which it deliberately is not yet.
* **Verified skill factory** (Sol §9, TZ Ph7). The TZ itself puts it after
  phases 1-6 are stable.
* **Mission IR ↔ world-fact binding for effect obligations** (TZ Ph1). The
  mechanism now exists on both sides (`MissionIR` obligations, `require_fresh`);
  binding them has no consumer until the compiler feeds an execution path, and
  building the binding first would be building it untested against real use.
* **Counterfactual layer** (Sol §6, charter §7). Correctly ordered after the
  world state, which only became real tonight.
