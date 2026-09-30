# Epoch 4 golden foundation — bounded evidence

Date: 2026-09-05; replay-denial update 2026-09-06. Workstream E.
**Epoch acceptance remains BLOCKED; E4-RT-001 local regression now passes.**
This is developmental integration evidence, not Epoch 4 release certification.
Baseline: `0f5f0c4c2d39b6d5003999be6bb877e2cbc7f989` (published plan).
Upstream observed after fetch: `d6b43cea0a1127bba7fa2cdabbd80dfa6da681bc`
on `claude/bossman-control-v03-43igbk`; no upstream kernel changes ingested here.

## Changed behavior and shared value

New `bossman-core/tests/test_epoch4_golden_foundation.py` exercises existing
execution boundaries. No production module, policy, finalizer, journal, CI,
permission, or dependency is modified. The tests make real duplicate effects
observable independently of executor success claims and receipt signatures.
The new Mission IR implementation is deliberately not required by these tests.

| Mission | Real path and oracle | Coverage and limits |
|---|---|---|
| G01 | Existing authenticated ASGI fixture bootstraps BCC task/agent; BCC policy → owner-test approval → canonical terminal registry → real Python subprocess → BCC fresh file SHA256 verification → signed TaskJournal; parent reopens exact bytes | Integration foundation only; runner invoked in process, no Mission IR dispatch or canonical BCC task finalization claim |
| G02 | Same real BCC path; command prints success without creating a file, or creates wrong bytes at the correct path; parent reads actual filesystem | Both cases execute a tool but do not complete CompoundRunner or produce a signed completed step |
| G06 | Canonical UCA/CompoundRunner/TaskJournal in a child interpreter; process exits abruptly after signed s1, before s2; fresh interpreter resumes | s1 signed record is unchanged, ledger is exactly s1/s2/s3, another restart dispatches nothing |
| G07 | Child fsyncs irreversible s2 append, then `os._exit(86)` before returning receipt; parent independently opens ledger and journal; two fresh resume processes | Ten deterministic payload seeds; intact journal parks s2 as unknown and never repeats it; not a full crash matrix |

For G06/G07, the executor is a constrained test-only append service backed by
a real file. It has **no deduplication**, so a replay produces a second line.
Policy permits only this fixed action and temp target. Observation independently
reopens that file. No model, network, external account, OS GUI, Windows desktop,
external payment, or remote Fleet runs. Abrupt `os._exit` bypasses Python cleanup;
this is actual process termination/restart, not hardware power-loss testing.
G01/G02 reuse the existing BCC fixture, including its owner-test approval method;
they do not claim product authorization was supplied automatically.

## Reproducible verification

Runtime: Linux x86_64, kernel 6.18.35; Python 3.12.13.
Interpreter used: `/tmp/bossman-epoch4-venv/bin/python`.
Run from the repository root with the shared, Core and Command Center packages
installed. Core-only environments skip the three BCC cases with an explicit
reason; subprocess/journal tests still run.

```bash
PYTHONPATH="$PWD:$PWD/bossman-core:$PWD/command-center" \
  /tmp/bossman-epoch4-venv/bin/python -m pytest \
  bossman-core/tests/test_epoch4_golden_foundation.py \
  bossman-core/tests/test_v3_command_center_adapters.py \
  bossman-core/tests/test_v3_compound_resume.py -q --disable-warnings
```

Initial new positive/negative foundation run: **17 passed in 28.80 seconds**.
Combined foundation, existing BCC adapter and compound resume regression:
**31 passed, 1 xfailed in 32.99 seconds; zero skips**.
The additional adversarial test reproduced E4-RT-001 as **1 failed / 17 deselected**
in 1.38 seconds before it was explicitly marked as a strict security xfail.
The xfail permits only `AssertionError`; unrelated exceptions are not hidden.
An unexpected pass fails the suite and requires reviewing/removing the marker.
An expected security failure is an open blocker even when pytest exits zero.

## Authenticated-journal follow-up (2026-09-06)

On a worktree including kernel patch `7347584`, the unchanged original
regression passed with `--runxfail`: **1 passed, 17 deselected**. After removing
the marker and strengthening denial/effect-oracle assertions, this selection
passed **46 tests in 44.18 seconds; zero skips/xfails**:

```bash
PYTHONPATH="$PWD:$PWD/bossman-core:$PWD/command-center" \
  /tmp/bossman-epoch4-venv/bin/python -m pytest -q \
  bossman-core/tests/test_epoch4_golden_foundation.py \
  bossman-core/tests/test_v3_authenticated_journal.py \
  bossman-core/tests/test_v3_command_center_adapters.py \
  bossman-core/tests/test_v3_compound_resume.py
```

This closes the reproduced byte-tampering fixture gap locally. It does not
prove general exactly-once semantics or resistance to restoring an older,
still-valid signed snapshot. Independent release CI and other epoch gates
remain required.

## E4-RT-001 — unsigned in-flight edit repeats irreversible effect

**Status: local regression verified on authenticated journal repair
`7347584b49d25ad366403a6e71e7c132641e6ab1`.**
The reproduction below records the original defect. Current schema 3 loading
rejects the altered whole-journal signature before constructing replay state.
The xfail has been removed only after the original raw regression passed; the
updated test additionally requires JournalIntegrityError on two new processes
and exact preservation of the rejected journal bytes for reconciliation.
Threat prerequisite: an actor or corrupted write can alter the journal JSON.
This is integrity checking of persisted execution state, not proof that an
ordinary remote model can write the journal directory.

Reproduction:

1. Start G07. s1 completes and is signed; s2 appends durably, then the process
   exits before any receipt. The independent ledger is `['0/s1', '0/s2']`.
2. Change **only** `steps[1].in_flight` from `true` to `false` in the journal.
   Preserve `STARTED` status, action/plan digests and signed s1 data.
3. Resume in another interpreter with the original plan.
4. The ledger becomes **`['0/s1', '0/s2', '0/s2']`**. Later verification fails
   because the count is two, but it cannot undo the duplicate irreversible effect.

Before the repair, `TaskJournal.validate()` authenticated completed steps; the
in-flight state did not have equivalent authentication. `CompoundRunner` checks `in_flight` before
its no-replay rule and does not reject the contradictory STARTED/false state.
Repair needs authenticated durable effect intent plus status consistency before
replay. Merely strengthening completed receipt signatures is insufficient.
Reconciliation must preserve ambiguous effects; do not reset/retry to get green.

```bash
PYTHONPATH="$PWD:$PWD/bossman-core:$PWD/command-center" \
  /tmp/bossman-epoch4-venv/bin/python -m pytest \
  bossman-core/tests/test_epoch4_golden_foundation.py \
  -k clearing_unsigned --runxfail -q
```

Other adversarial probes in this batch reject forged signed receipt fields,
changed task identity and altered plan digest with zero additional ledger writes.

## Required evidence for eventual certification

For each accepted run, record full SHA, dirty-tree flag, OS/Python, command,
seed, start/end, tier, boundary substitutions, expected/observed effects,
counts, skips/xfails, retry count, model cost and independently hashed artifacts.
These initial tests use temporary fixtures; they do not publish an immutable
release evidence bundle. The associated Git commit identifies this fixture,
not an assertion that all required golden missions pass at that commit.

Next integration gates: preserve Mission IR digest/effect bindings into real
intake and finalization; independent integrated release review of the repaired
kernel; full crash
matrix and Windows/live-model missions remain open. No epoch milestone closes
from this foundation alone.

## Rollback

Revert only these two new files. There is no schema migration or runtime flag.
Retain the original E4-RT-001 evidence; record this local regression result in
the canonical blocker ledger without implying complete V3/V4 acceptance.
Runtime rollback must retain authenticated journal validation as required in
`../recovery/AUTHENTICATED_JOURNAL.md`; never resume these snapshots with an
older unauthenticated reader.
