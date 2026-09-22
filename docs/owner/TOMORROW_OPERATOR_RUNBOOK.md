# Bossman 1.0 — Tomorrow Operator Runbook

Date: 2026-09-22  
Purpose: one authoritative entrypoint for the next owner-hardware session.  
Canonical branch: `release/bossman-owner`.

This document resolves sequencing between README, CURRENT_STATE, CONTINUATION, OWNER_ACCEPTANCE and the EVO/training specifications. It does not certify a build.

## 0. Source-of-truth order

When documents disagree, use this order for tomorrow's run:

1. actual remote `release/bossman-owner` HEAD and its Git history;
2. `tools/release_candidate.json` + `tools/exact_sha_certify.py` for candidate/CI truth;
3. this runbook for sequencing;
4. `owner-repair/CONTINUATION.md` and `owner-repair/repair-ledger.md` for repair state;
5. `OWNER_ACCEPTANCE.md` for owner scenarios;
6. `KNOWN_LIMITATIONS.md` for unresolved scope;
7. EVO/frontier/Qwen documents for training after release blockers are closed.

Historical README/archive/checkpoints are evidence for their own SHA only.

Never infer readiness from the latest branch tip, an old certificate, a PR body count, or a green workflow on another SHA.

## 1. Morning preflight — no code changes yet

Record:
- REMOTE_HEAD;
- candidate declaration;
- exact mandatory workflow list from the certifier;
- all workflow/job conclusions for the candidate SHA;
- Windows artifact source SHA and SHA-256 if one exists;
- open repair items;
- actual model endpoint IDs and runtime versions on owner hardware.

Before touching production, classify the situation:

### A — candidate already exact-SHA green
Do not create a documentation-only commit. Download/install that exact artifact and start owner acceptance.

### B — software P0/P1 or mandatory CI regression exists
Reproduce only the failure, fix root cause, add regression, push, declare a new candidate, restart exact-SHA certification.

### C — only owner-hardware evidence is missing
Do not modify code. Start the owner run.

This prevents the moving-candidate loop.

## 2. Freeze discipline

Once a candidate SHA enters the final matrix:
- no README/scorecard/EVO/docs push to canonical;
- no unrelated cleanup;
- no version bump;
- no candidate-label churn.

All observations go to local evidence or an audit/evidence destination that does not move the candidate.

Only a reproduced release-relevant defect may break freeze.

A broken freeze creates a new candidate and invalidates unfinished exact-SHA certification for the old tip.

## 3. Known repair truth entering tomorrow

Already repaired and regression-covered; re-test on installed candidate, do not redesign without a reproduced regression:
- B4 browser download;
- AP-ALL approvals filtering;
- TEL-001 model telemetry;
- CU-VERIFY;
- CU-APPROVAL;
- F-17 rendered-page race;
- MEDIA-HASH;
- MEDIA-CANCEL.

Open/owner-run-sensitive items from the latest handoff:
- MEDIA-RESTART durable/reconciliation behavior;
- Computer Use STOP/resume/fresh-target behavior on the final installed bytes;
- Video Studio real model product path;
- Qwen coaching/holdout;
- final multi-agent, MVČR, long-session and independent red-team.

The old 62-fail local suite is not 62 product defects: the repair handoff traced it primarily to missing FFmpeg/MCP harness dependencies plus F-17. Tomorrow must use the shipped runtime/artifact, not recreate that broken harness.

## 4. Machine ownership

Only one actor owns each mutable resource at a time:
- Windows desktop/mouse/keyboard;
- Bossman backend restart;
- MAIN endpoint;
- FAST endpoint;
- media GPU workload;
- canonical Git integration.

Use an explicit local lease/handoff record.

Never kill all `python.exe` processes. Stop only verified PIDs belonging to the current test.

Do not run LLM throughput and video-generation benchmarks simultaneously.

## 5. Installation truth

Install the candidate outside the source checkout into a new folder.

Verify before launch:
- application ZIP SHA-256;
- embedded source SHA/manifest;
- shipped Python;
- Chromium;
- FFmpeg/ffprobe;
- required MCP/runtime dependencies;
- launcher paths contain no developer-specific absolute path.

Do not repair the installed candidate with pip, PYTHONPATH or copied source files. A packaging defect is a product defect and requires a new artifact.

Keep the previous known installation for rollback until the candidate passes.

## 6. Models

Discover runtime truth from endpoints; do not trust documentation labels alone.

Record for MAIN/FAST:
- model ID;
- checkpoint/GGUF hash/revision;
- quantization;
- llama.cpp/runtime revision;
- backend;
- context;
- tool/vision capability;
- ports;
- RAM before/after load.

Expected current roles are Qwen3.8-27B MAIN and Qwen3.6-35B-A3B FAST, but actual endpoint identity wins.

Do not change model weights during the benchmark.

## 7. Owner-run order

Run in this order so one failure does not contaminate later evidence:

1. doctor/start/auth/Flight Recorder;
2. MAIN + FAST direct health and Bossman routing;
3. memory/project isolation + restart;
4. files/PDF/DOCX/CSV/JSON;
5. browser normal navigation + download;
6. Computer Use focus/type/save/STOP/resume/re-observe;
7. approvals + replay/cross-action/restart;
8. coding and multi-agent;
9. Web Designer;
10. Image Studio;
11. Video Studio local generation;
12. Music Studio;
13. uncertain-outcome recovery;
14. MVČR preparation;
15. authorized Telegram round-trip;
16. Qwen apprentice benchmark/coaching;
17. independent red-team;
18. long-session/restart smoke.

For every step record PASS / FAIL / BLOCKED / NOT_RUN / OWNER_REQUIRED and evidence.

A correct human stop at login/payment/signature/submission is OWNER_REQUIRED, not product failure and not completed submission.

## 8. Video/media protocol

Editing/export and AI generation are different claims.

For a local AI-video PASS require:
- real model execution trace;
- new output generated during this run;
- model/revision/runtime/backend;
- prompt/input hash and seed where available;
- elapsed time and memory;
- output SHA-256;
- ffprobe;
- full decode;
- sampled-frame/visual sanity check.

Do not call FFmpeg testsrc, imported media, interpolation or an old sample an AI generation PASS.

Test cancel and restart/reconciliation. If a job disappears after provider restart, report the exact behavior; do not silently resubmit an uncertain job.

## 9. Qwen apprenticeship

Follow `docs/evo/LOCAL_QWEN_APPRENTICE_BENCHMARK.md`.

Important sequencing:
- freeze benchmark/hidden tests before teaching;
- run unassisted baseline first;
- Opus/frontier gives the smallest hint, not a patch;
- Qwen retries;
- verified lessons enter the real Bossman learning/skills path;
- restart Bossman;
- run unseen A/B holdout;
- teacher-patch remains separate from student success.

Do not train around a software/security defect. Fix the product first.

Report `WEIGHTS_UNCHANGED` unless actual isolated weight training occurs under a later explicit experiment.

## 9A. Claude operating mode — auditor first

During coding/repair and training, use the local Qwen as the primary implementer.

Default:
1. Qwen reads the scoped task.
2. Qwen writes the code.
3. Qwen writes/updates regression tests.
4. Qwen runs the tests and inspects the result.
5. Only then send a compact evidence packet to Claude/frontier.
6. Claude audits the diff/tests and returns ACCEPT or a minimal directional correction.
7. Qwen applies the correction and retests.
8. Claude reviews only the changed delta/evidence.

Claude should not repeatedly scan the whole repository or write the first patch. Preserve frontier capacity for architecture, security, difficult root causes and final review.

Teacher patch is last resort and must be labeled TEACHER_PATCH. After such a patch, Qwen still has to inspect and test it, and the generalized verified lesson is stored for future local attempts.

Exception: credible P0 security/data-loss risk, unavailable local runtime, or a task outside the local student's permitted tool boundary.

Record frontier/local usage so tomorrow produces an actual answer to: what percentage can Bossman/Qwen repair locally, what improves after coaching, and where frontier intelligence is still necessary.


## 10. Evidence hygiene

Every run must bind evidence to:
- source SHA;
- installed artifact SHA-256;
- config fingerprint;
- model/runtime revisions;
- task/run IDs;
- timestamps;
- actor (owner/student/teacher/verifier/auditor).

Never commit secrets, cookies, personal documents, raw private screenshots, model weights or giant logs.

Safe Git evidence: fixtures, manifests, summaries, hashes, regressions and sanitized logs.

## 11. Failure routing

When something fails, classify before acting:

- PRODUCT_CODE;
- PACKAGING;
- MODEL_QUALITY;
- MODEL_RUNTIME;
- HARNESS;
- ENVIRONMENT;
- EXTERNAL_SERVICE;
- OWNER_REQUIRED;
- INSUFFICIENT_EVIDENCE.

Then:
- PRODUCT_CODE/PACKAGING -> fix, regression, new SHA/artifact;
- MODEL_QUALITY -> coaching/skill candidate after product path is proven;
- MODEL_RUNTIME -> runtime/config investigation, no product fake-success;
- HARNESS -> fix harness without weakening product test;
- OWNER_REQUIRED -> stop at boundary and continue independent scenarios.

Never relabel a product defect as OWNER_REQUIRED.

## 12. Red-team independence

The final attacker should not receive repair solutions, teacher lessons or expected attack answers before its first pass.

Minimum attack areas:
- prompt/lesson poisoning;
- approval replay/cross-action;
- stale screen/focus;
- STOP race;
- duplicate effect;
- hard kill/uncertain outcome;
- malformed tool/schema;
- download/media corruption;
- model outage;
- project/context contamination;
- worker/verifier disagreement;
- fake completion.

A new P0/P1 breaks freeze and starts a new candidate cycle.

## 13. Stop conditions

### READY_FOR_1_0_PROPOSAL
Only if:
- P0=0;
- software P1=0;
- mandatory exact-SHA matrix is green;
- exact Windows artifact is installed and accepted;
- release-critical owner scenarios pass in declared scope;
- no known fake-success critical path;
- independent red-team leaves no unresolved P0/P1.

### NOT READY
Report one concrete blocker and preserve evidence. Do not create a ceremonial release.

Training gain is not required to claim a safe product if the learning feature is honestly scoped, but training evidence must not be fabricated.

## 14. End-of-day handoff

Produce one concise owner report:
- FINAL/TESTED_SHA;
- Windows artifact + SHA-256;
- CI matrix;
- P0/P1/P2;
- owner scenario score;
- MAIN/FAST identities and performance;
- real video result;
- Qwen unassisted/coached/teacher-patch scores;
- holdout A/B;
- red-team;
- remaining limitations;
- single next action.

No final documentation commit after certification if it would move the tested branch. Attach post-test evidence without changing the certified source SHA.
