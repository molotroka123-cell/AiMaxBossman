# Bossman 1.5 — Economy Learning Swarm

Status: IMPLEMENTED ON FEATURE BRANCH / OWNER RUN REQUIRED

Branch: feat/bossman-1.5-economy-learning-20260924

This lane stays separate from the frozen Bossman 1.0 release.

## Goal

Move bulk public-video research, first-line verification and routine repair away
from scarce Codex/frontier budget while keeping Bossman's existing evidence,
approval, privacy, memory and cost boundaries.

Pipeline:

    public K1m6a YouTube videos
      -> local captions / ASR / frame sampling / local vision
      -> UNVERIFIED episode evidence
      -> Jev typed route
      -> 3 parallel Nemotron free workers
      -> Ling 3.0 Flash Fin free verifier/tester/coder
      -> deterministic tests
      -> optional one-call paid GLM-5.3-Flash finalizer
      -> deterministic tests
      -> Aster audit/control

Aster does not write product code in this lane. Codex is reserved for integration,
unresolved blockers and final owner review rather than bulk work.

## Model roles

Bossman syncs the live OpenRouter catalog before setup. Exact ids:

- nvidia/nemotron-3-ultra-550b-a55b:free — three parallel evidence workers.
- inclusionai/ling-3.0-flash-fin:free — finance verifier/tester/bounded coder.
- z-ai/glm-5.3-flash — paid finalizer.
- Jev — typed workflow routing only.
- Aster — audit/control only.

The setup refuses a supposed free worker unless the live catalog reports zero
input and output pricing. GLM must have known live pricing.

References checked when this lane was written:

- https://openrouter.ai/nvidia/nemotron-3-ultra-550b-a55b-20260604:free
- https://openrouter.ai/inclusionai/ling-3.0-flash-fin:free
- https://openrouter.ai/z-ai/glm-5.3-flash

The runtime catalog remains authoritative because price and availability change.

## Jev authority boundary

Implemented in command-center/bcc/features/economy_swarm.py.

The deterministic policy constructs the allowed action set before Jev sees it.
Jev can choose only inside that set.

Paid GLM is absent unless all are true:

1. paid escalation was explicitly enabled for this run;
2. paid budget remains;
3. Ling was already attempted;
4. Ling returned FAIL or BLOCKED;
5. unresolved blockers remain;
6. no GLM call was already used.

Maximum GLM calls per economy workflow: 1.

If Jev is required and unavailable, the workflow becomes BLOCKED_JEV. It never
silently falls through to paid work.

## Bossman-only inference path

tools/bossman_15_setup.py:

- discovers the running Bossman;
- reads Bossman's canonical OpenRouter provider;
- syncs Bossman's OpenRouter catalog;
- pins exact model ids into Bossman's registry;
- runs Bossman's model probes unless disabled;
- creates or updates five Bossman agents;
- never reads or prints the OpenRouter key.

tools/bossman_15_economy_run.py:

- submits every inference as a normal Bossman task;
- reads Bossman task/run cost telemetry;
- calls /api/economy/route between stages;
- never calls OpenRouter directly;
- rejects non-zero reported spend from free workers;
- runs deterministic pytest after code repair;
- creates an Aster audit packet.

tools/worker_client.py remains a diagnostic/fleet probe. It is not the production
training path for this owner run.

## K1m6a YouTube batch

Implemented in tools/k1m6a_youtube_batch.py.

Default source:
https://www.youtube.com/@k1m6a/videos

Default inclusive owner window:
2026-08-14 through 2026-08-27

The dates remain CLI arguments.

Discovery uses yt-dlp metadata. Every selected public video then uses the
existing tools/youtube_trader_ingest_auto.py path:

- captions when available;
- local ASR fallback;
- frame sampling;
- local vision;
- candidate_cases.jsonl;
- future in-video outcome labels when available.

Teacher material stays UNVERIFIED and is not written into procedural trading
memory automatically.

## Learning: skills, workflows and memory

Role skill profiles:

- Nemotron transcript: external-evidence-check, context-builder.
- Nemotron chart: measure-do-not-assume, external-evidence-check.
- Nemotron strategy: variant-analysis, negative-control.
- Ling: systematic-debugging, test-driven-development,
  verification-before-completion.
- GLM: safe-code-change, differential-review, proof-before-done.
- Aster: repo-audit, permission-auditor, honest-verdict.

This is workflow and memory learning. It does not claim model weights changed.

tools/bossman_15_memory_sync.py writes only evidence-backed operational lessons
into the existing canonical LessonBook. Example lessons are free-first routing
and proof-before-DONE.

YouTube trading hypotheses are deliberately excluded from this generic memory
sync. They stay in trading quarantine and must satisfy existing independent
episode, lookahead and out-of-sample promotion gates.

## Fresh scenarios and distillation

The branch includes prepared owner scenarios for:

- hidden repo defect;
- misleading test/harness;
- Windows path;
- false DONE;
- tool-schema correctness.

It also carries:

- tools/distill_recorder.py
- tools/distill_export_bossman.py
- tools/fleet_green_probe.py
- tools/owner_scenarios_20260924.py

Distilled records are evidence and training data candidates, not automatic
weight updates.

## Provider resilience

The fleet work includes bounded retries for transient free-gateway/OpenRouter
errors, including HTTP-200 responses containing an in-body transient error.

A transient provider failure is not a model-quality failure and does not grant
permission to jump to paid work.

## Owner commands

Setup:

    python tools\bossman_15_setup.py --glm-budget-usd 0.25 --out owner-test-pack\v15\setup.json

Discover requested date window:

    python tools\k1m6a_youtube_batch.py --start 2026-08-14 --end 2026-08-27 --discover-only --root owner-test-pack\v15\youtube

Run local ingest:

    python tools\k1m6a_youtube_batch.py --start 2026-08-14 --end 2026-08-27 --root owner-test-pack\v15\youtube

Run economy swarm:

    python tools\bossman_15_economy_run.py --batch-manifest owner-test-pack\v15\youtube\batch-manifest.json --out owner-test-pack\v15\economy --repo-polish --allow-paid --glm-budget-usd 0.25

Sync verified workflow lessons:

    python tools\bossman_15_memory_sync.py owner-test-pack\v15\economy\economy-report.json

## Money rule

Intended cost shape:

free bulk -> free verifier/repair -> maybe one cheap paid finalizer

Every run records tokens, known/unknown cost, free-worker cost violations,
number of GLM calls, paid spend and whether Codex bulk work was avoided.

Unknown cost is not free. No auto-recharge.

## Trading boundary

This lane is READ_ONLY_LEARNING.

No exchange order endpoint is required. No YouTube lesson, model answer, Jev
route or historical case authorizes a live trade.

## Definition of Done for the economy-learning lane

Code-ready:

- economy router implemented and tested;
- exact models configured from Bossman's live catalog;
- three Nemotron roles;
- Ling free verifier/coder;
- one-call GLM finalizer;
- Jev hard allow-set routing;
- date-bounded YouTube ingest;
- Bossman-only model orchestrator;
- deterministic verification after repairs;
- workflow memory sync;
- Aster audit packet;
- fresh scenarios and distillation utilities.

Owner-live:

- requested video list recorded;
- each selected video has local evidence or named blocker;
- each ingested video receives three free worker passes;
- Ling verification is recorded;
- free workers show zero billed cost;
- GLM is used zero or one time and stays inside budget;
- targeted tests are green;
- Aster audits independently;
- workflow lessons survive restart and help on an unseen related task;
- trading lessons remain quarantined unless their independent promotion gates pass.

Only owner-run evidence may move this lane from IMPLEMENTED to OWNER_LIVE_PASS.
