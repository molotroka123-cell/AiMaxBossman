# Local Qwen Apprentice Benchmark & Verified Training

Date: 2026-09-22  
Status: SPECIFICATION / owner-hardware execution required.  
Canonical line: `release/bossman-owner`.

## Goal

Turn expensive frontier repair work into reusable verified training for the local Bossman instead of repeatedly sending every bug to a frontier coder.

Target operating loop:

`FAST local attempt -> MAIN local attempt -> tests/verifier -> frontier hint -> local retry -> frontier patch only if still needed -> verified lesson -> unseen holdout -> promotion proposal`.

This specification does not claim that Qwen already matches a frontier coder and does not authorize autonomous promotion to stable.

## Students and teacher

Students are the exact local models discovered from the owner installation at runtime. The current expected roles are:
- MAIN: Qwen3.8-27B;
- FAST: Qwen3.6-35B-A3B.

Do not trust these names from documentation alone. Record the actual model IDs, revisions/quantizations, llama.cpp/runtime revision, context, tool configuration and artifact hashes used in the run.

The frontier model is the teacher/reviewer. It must not silently do the student's work and then record STUDENT_PASS.

## Phase A — freeze the exam

Before showing repair solutions to the student, create an immutable benchmark manifest.

Use historical defects whose pre-fix state and verified repair are available, including:
- B4 browser download;
- AP-ALL approval filtering;
- TEL-001 model telemetry;
- CU-VERIFY;
- CU-APPROVAL;
- F-17 UI render-settle race;
- sd.cpp MEDIA-HASH;
- sd.cpp MEDIA-CANCEL.

Add fresh synthetic defects that were never shown to the student. Holdout answers, patches and hidden tests stay unavailable to the student and training memory until scoring is complete.

Every case must include:
- source/base SHA or isolated fixture;
- task text;
- allowed tools;
- time/token/attempt budget;
- visible tests;
- hidden verifier tests;
- expected observable behavior;
- safety constraints;
- evidence manifest.

Do not mutate canonical stable to recreate old bugs. Use isolated worktrees/fixtures.

## Phase B — baseline

For every case run, in order:

1. FAST alone when the task is inside its intended role.
2. MAIN alone.
3. MAIN with existing Bossman memory/skills but without new teacher lessons.

The student must inspect, edit, run and verify through Bossman's normal coding/tool path.

Record:
- solved/failed;
- root cause identified;
- patch correctness;
- visible and hidden tests;
- regressions;
- attempts;
- wall time;
- tool errors;
- teacher interventions = 0;
- model/runtime/config fingerprint.

A correct explanation without a working verified patch is not a coding PASS.

## Phase C — teacher coaching

When MAIN fails, the frontier teacher first provides the smallest useful correction, not a ready-made patch.

Sequence:

`student attempt -> verifier failure -> teacher hint -> student retry -> verifier`.

Escalate hints gradually:
1. point to the failing invariant/subsystem;
2. identify the likely root-cause class;
3. suggest a verification strategy;
4. only after repeated failure may the teacher prepare a patch.

Classify outcomes:
- STUDENT_UNASSISTED_PASS;
- STUDENT_COACHED_PASS;
- TEACHER_PATCH;
- FAIL.

Never merge these categories into one success percentage.

## Phase D — verified learning record

Only a reproduced failure followed by a verified correction can become a lesson.

Store the lesson through Bossman's existing memory/learning/skills path, not only Markdown.

Each episode records:
- task class;
- observed failure;
- verified root cause;
- correction;
- generalized strategy;
- executable verifier/regression;
- applicability and counterexample;
- model/runtime;
- provenance;
- assistance level;
- source SHA/evidence hashes;
- privacy class;
- candidate/verified/quarantined state.

Do not store hidden chain-of-thought. Store actions, concise explanations, diffs, tests and outcomes.

A teacher statement is untrusted until executable evidence verifies it.

## Phase E — restart and transfer

After lessons are stored:
1. restart Bossman;
2. prove the lesson is still retrievable;
3. give a new analogous task not present in training;
4. do not provide the teacher hint;
5. verify whether the student independently applies the generalized strategy.

Training is not considered effective merely because memory contains the lesson.

## Phase F — A/B holdout

Compare the same model/runtime/tools under isolated profiles:

A. baseline profile without the newly verified lessons;
B. trained profile with them.

Use new holdout tasks and fixed budgets.

Measure:
- pass@1;
- verified completion rate;
- attempts;
- tool/schema errors;
- teacher interventions;
- time to verified result;
- regressions;
- restart retention;
- context/project leakage.

Report sample size and uncertainty. A small run is preliminary evidence, not universal intelligence gain.

If B does not improve or causes regression, keep the lesson in shadow/quarantine rather than promoting it.

## Phase G — repair escalation policy

For future Bossman bugs:

1. deterministic/unit tools first where sufficient;
2. FAST for simple classification, logs and small fixes;
3. MAIN for normal coding/agent repairs;
4. MAIN + verified skills;
5. frontier teacher hint;
6. MAIN retries;
7. frontier coder writes the patch only for the unresolved remainder;
8. independent verifier/red-team;
9. verified lesson enters the training store.

The goal is to reduce frontier consumption while increasing verified local autonomy. Cost savings are measured, not assumed.

## Frontier economy protocol — Qwen writes, Claude audits

Default rule for coding/repair tasks:

`Qwen implements -> Qwen writes/updates tests -> Qwen runs tests -> Claude audits evidence -> Qwen corrects -> verifier decides`.

Claude/frontier is not the default implementation engine. Its normal role is:
- audit the student's diff and test evidence;
- detect missing invariants, security/safety gaps and false success;
- point Qwen toward the next highest-value correction;
- verify that the generalized lesson is safe to store;
- act as final architecture/security reviewer where local confidence is insufficient.

### Context economy

Before a frontier call, Bossman prepares a compact audit packet:
- task and expected behavior;
- relevant file list;
- Qwen diff;
- failing/passing test names and concise logs;
- changed contracts/invariants;
- model/runtime/config fingerprint;
- open question.

Do not send the whole repository, giant logs, repeated unchanged context or hidden reasoning unless the auditor explicitly needs a narrow additional file.

Reuse hashes/references for unchanged evidence.

### Escalation

Claude must not edit production before at least one genuine MAIN attempt, except:
- confirmed/credible P0 security or data-loss risk where allowing the student to continue would be unsafe;
- the local model/runtime is unavailable;
- the task is outside the student's permitted tool boundary.

Normal sequence:
1. Qwen produces patch + regression.
2. Qwen self-tests.
3. Claude returns ACCEPT or REJECT + the smallest actionable hint.
4. Qwen revises and retests.
5. Claude re-audits only the delta/evidence.
6. After repeated verified failure, Claude may supply a patch.
7. Qwen must then inspect/explain/test that patch before it can become a verified lesson.

A Claude-authored patch is always TEACHER_PATCH. It is never counted as student success.

### Frontier budget accounting

For every frontier intervention record:
- reason for escalation;
- context bytes/tokens if available;
- response tokens if available;
- hint level;
- whether Qwen succeeded after the hint;
- whether a teacher patch was ultimately required.

The optimization target is `frontier cost per verified result`, not minimum tokens at the expense of correctness.

Do not spend frontier calls re-reading green unchanged subsystems. Prefer deterministic tests and local verification first.

### Training consequence

Every successful coached repair becomes a candidate lesson only after executable verification.

The lesson must teach the strategy that allowed Qwen to solve the task, not copy Claude's final patch verbatim.

On a later analogous task, Claude stays silent until Qwen has attempted it. A transferred skill is proven only when the local student succeeds without teacher help.

## Phase H — promotion boundary

Memory/skill learning does not change model weights.

Optional LoRA/QLoRA is a separate later experiment and requires:
- curated permitted dataset;
- explicit training configuration;
- isolated adapted model;
- unseen holdout;
- regression/catastrophic-forgetting checks;
- security/tool-use checks;
- comparison with the unmodified base model;
- owner-approved promotion.

Until then report `WEIGHTS_UNCHANGED`.

Stable Bossman never self-approves its own replacement.

## Acceptance criteria

The apprentice loop is accepted only when:
1. exact student model/runtime/config is recorded;
2. historical benchmark is isolated from canonical;
3. at least 5 fresh holdout tasks exist;
4. unassisted/coached/teacher-patch results are separate;
5. lessons survive restart through the real Bossman learning path;
6. at least one lesson transfers to a new task without teacher help;
7. baseline and trained profiles are isolated;
8. hidden tests are not leaked into training;
9. regressions and poisoned lessons are rejected/quarantined;
10. no permission/budget/privacy boundary is weakened;
11. all results have executable evidence;
12. frontier usage and local usage are measured;
13. failures remain visible rather than relabeled;
14. weights are reported unchanged unless actual weight training occurred.

## Tomorrow's owner-hardware sequence

This benchmark starts only after software-fixable release P0/P1 for the candidate are closed and the candidate Windows artifact is ready.

Then:
1. verify artifact SHA and clean install;
2. verify MAIN/FAST endpoints;
3. run baseline apprentice benchmark;
4. run teacher coaching only on failed cases;
5. store verified lessons;
6. restart Bossman;
7. run unseen holdout A/B;
8. execute normal owner acceptance;
9. execute independent red-team;
10. publish the evidence pack.

If a new release P0/P1 is found during the run, repair it first, rebuild a new exact-SHA artifact, then resume the affected tests. Do not train around a product defect that should be fixed in code.

## Evidence outputs

Create under an owner-training evidence directory:
- `BENCHMARK_MANIFEST.json`;
- `BASELINE_RESULTS.json`;
- `COACHING_EPISODES.jsonl`;
- `LESSON_PROMOTION.json`;
- `HOLDOUT_RESULTS.json`;
- `FRONTIER_USAGE.json`;
- `LOCAL_USAGE.json`;
- `TRAINING_SUMMARY.md`.

Large/private raw artifacts remain local; Git receives sanitized manifests, summaries, hashes and safe fixtures only.

## Phase A execution 2026-09-22 — frozen exam on four closed defects

Phase A is executed for four cases taken from defects the teacher closed on the owner
machine in this session: TRAIN-1 (English-only text encoders), TRAIN-2 (first Studio
listing timeout), HOLDOUT-1 (env-file line concatenation) and HOLDOUT-2 (video
container bytes).

- manifest: `owner-repair/coaching-exam-20260922/manifest.json`
- runner: `tools/coaching_exam.py` (reuses `learning.lessons.LessonBook` and the
  backends of `tools/coaching_runner.py`; no second learning engine)
- harness proof on a MOCK student: `command-center/tests/test_coaching_exam.py`
- isolation, metrics and the single live-run command:
  `owner-repair/coaching-exam-20260922/README.md`

Answers, break/fix edits, hint ladders and hidden verifier tests live OUTSIDE every git
worktree in a sealed directory; the repository holds only their sha256. The live run on
the local students is NOT_RUN until the owner starts the models.
