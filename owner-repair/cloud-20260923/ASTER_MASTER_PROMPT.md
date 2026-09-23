# ASTER — NIGHT CONVERGENCE MASTER PROMPT
## Bossman 1.1 + Terminal 1.2 → ONE BRANCH → WORKING PROTOTYPE IN main

Repository: `molotroka123-cell/AiMaxBossman`

Owner directive, 2026-09-23:

**Tonight converge all useful, tested Bossman work into exactly one working integration line:**
`claude/bossman-cloud-closure-owner-a6s1ki`

**Tomorrow the owner must be able to run the working prototype from `main`.**

This is an execution directive, not a roadmap exercise.

---

## 0. NON-NEGOTIABLE OUTCOME

By the end of this pass there must be:

1. one convergence branch:
   `claude/bossman-cloud-closure-owner-a6s1ki`;
2. one final candidate SHA;
3. one Windows artifact produced from that exact SHA;
4. one tested Terminal Run path;
5. one working Bossman 1.1 bounded self-improvement path;
6. a clean, reviewable promotion path into `main`;
7. if all mandatory gates pass, `main` advanced to the final candidate without rewriting history;
8. if a mandatory gate fails, DO NOT fake readiness and DO NOT move `main`; leave the exact blocker and candidate SHA.

Do not create `final2`, `night-final`, `aster-final`, `main-new`, or another product branch.

Do not force-push.

Do not rebase shared history.

Do not silently drop commits.

---

## 1. FETCH CURRENT TRUTH FIRST

Run:

```
git fetch --all --prune
```

Before modifying anything, record the current SHAs of:

- `claude/bossman-cloud-closure-owner-a6s1ki`
- `release/bossman-owner`
- `integrate/owner-final-20260922`
- `codex/bossman-v1.1-evolution`
- `main`

At the time this prompt was updated:

- cloud convergence was at `7be4081c...`;
- `main` was `799fc3dd...`;
- GitHub comparison showed cloud convergence **1030 commits ahead of main and 0 behind**.

Those values are only an orientation point. Fetch again and trust current remote truth.

If `main` is still an ancestor of the final convergence SHA, promotion should be a normal fast-forward.

If it is no longer an ancestor, merge current `main` into the convergence branch first, resolve by meaning, test again, then merge normally. Never overwrite `main`.

---

## 2. READ MANDATORY CONTEXT

Read before changing architecture:

- `AGENTS.md`
- `CLAUDE_NEXT_ACTION.md`
- `docs/evo/BOSSMAN_1_1_NORTH_STAR.md`
- `owner-repair/cloud-20260923/CHECKPOINT.md`
- `owner-repair/cloud-20260923/CONVERGENCE_1_1.md`
- `docs/terminal/TERMINAL_RUN_1_2_MASTER.md`
- `docs/terminal/ACCEPTANCE_AND_OWNER_RUN.md`
- `docs/terminal/CLAUDE_TEACHER_TERMINAL.md`
- `docs/owner/TERMINAL.md`
- `docs/evolution/V1_1_FINAL_HANDOFF.md`
- current exact-SHA certifier and Windows bundle scripts.

North Star:

> Bossman 1.1 = verified continuous self-improvement. Terminal 1.2 is only another control surface over the same Bossman. After the first accepted owner run, the 4–6 day experiment targets measurable transfer and increasingly autonomous owner-approved work with commercial value.

Do not turn Terminal into another backend, another memory store, another task queue or another agent runtime.

---

## 3. ONE PRODUCT, ONE BACKEND

The target architecture remains:

```
Owner in CMD / Windows Terminal ----+
Claude Code via JSONL --------------+
Dashboard --------------------------+--> SAME Bossman API/backend
Telegram ---------------------------+       |
                                            +-- same tasks
                                            +-- same files/projects
                                            +-- same memory/LearningStore
                                            +-- same models
                                            +-- same skills
                                            +-- same approvals
                                            +-- same event/evidence ledger
                                            +-- same Computer Use
                                            +-- same coding path
                                            +-- same evolution state
```

No `terminal-memory`.

No second scheduler.

No second Telegram poller.

No duplicate model registry.

No direct-to-model shortcut that bypasses Bossman policies.

---

## 4. CONVERGE ALL USEFUL WORK INTO THIS BRANCH

Target branch:

`claude/bossman-cloud-closure-owner-a6s1ki`

Claude and Aster may both work, but all final integration lands here.

Inspect and preserve useful delta from:

- current release owner line;
- current integration owner line;
- `codex/bossman-v1.1-evolution`;
- Aster audit findings;
- current Terminal 1.2 work;
- Windows owner/bundle fixes;
- memory/coaching work;
- coding sidecar/OpenHands-compatible path;
- model profiles/bake-off;
- Bossfield or other explicitly owner-approved work.

For every incoming block classify it:

`ALREADY_PRESENT / UNIQUE_AND_USEFUL / SUPERSEDED / CONFLICTING / DOC_ONLY / TEST_ONLY / UNSAFE_OR_UNPROVEN`.

Merge by meaning, not by branch name.

Do not merge entire old histories blindly when the useful implementation already exists.

---

## 5. CLOSE THE REMAINING 1.1 SURFACE

The underlying evolution pieces already exist. Finish the product surface around them.

Required:

### CLI / owner commands

`tools/bossman_evolve.py` should expose coherent commands for:

- `loop`
- `status`
- `pause`
- `resume`
- `stop`
- `gate`
- `report`
- `soak`

These must operate on durable state and not depend on source-checkout-only paths.

### API

Finish the existing Bossman API surface for evolution:

- status
- start
- pause
- resume
- stop
- report

Use the existing process-tree management and policy layer.

No new server.

### Telegram

In the EXISTING owner companion only:

- `/evolution_status`
- `/evolution_pause`
- `/evolution_resume`
- `/evolution_stop`
- `/evolution_report`

Do not add a second bot poller.

### Gate truth

A MOCK_MODEL can prove plumbing only.

It must never produce:

`AUTONOMOUS_SELF_IMPROVEMENT_READY`

or equivalent.

---

## 6. FINISH TERMINAL 1.2 FOR TOMORROW

Terminal is tomorrow's preferred owner/teacher interface.

The intended launch is:

```
Bossman-CLI.cmd
```

or:

```
bossman.cmd
```

Claude Code should be able to drive Bossman headlessly:

```
bossman.cmd status --json
bossman.cmd -p "..." --output-format stream-json
bossman.cmd exec --input-file task.json
bossman.cmd events <task_id> --follow
bossman.cmd result <task_id> --json
bossman.cmd code "..." --allow ... --verify ... --json
bossman.cmd stop <task_id>
bossman.cmd stop --all
```

Required before freeze:

- `prompt_toolkit` and its locked dependency set included in the Windows bundle;
- Cyrillic-safe input/output;
- multiline paste;
- command/path completion where implemented;
- Ctrl+C semantics tested;
- reconnect/replay cursor tested;
- no duplicate task after reconnect;
- `code` and `/diff` path tested;
- memory/skills/tools views use real backend state;
- `stop --all` tested;
- resume after restart tested;
- no secret printed in stdout/history/argv;
- non-TTY JSON mode contains no ANSI or banners.

Thinking display rule:

Show model reasoning only when the actual provider returns reasoning content allowed for display. Otherwise show state such as `thinking...`, elapsed time, current stage and tool activity. Never fabricate hidden reasoning.

---

## 7. WINDOWS INSTALLED-PRODUCT TRUTH

Tomorrow's prototype is not accepted from source checkout.

Build a Windows ZIP from the final immutable candidate SHA.

From a fresh unpacked directory, prove:

- launcher starts the expected build SHA;
- no hidden dependency on repository checkout;
- embedded Python imports the shipped packages;
- coding path can run tests under the installed runtime;
- Terminal launchers work;
- STOP works;
- memory/task state survives restart as designed;
- browser/Computer Use startup is honest;
- model config can be read;
- missing optional hardware/model remains explicit, not fake PASS.

Preserve the existing Windows bootstrap fixes for embedded Python.

Do not rely on PYTHONPATH/cwd behavior that `python -I` or the embedded runtime ignores.

---

## 8. REAL SELF-REPAIR PROTOTYPE GATE

The prototype must be prepared for tomorrow's owner-hardware run.

The required real scenario is:

> "Bossman, improve yourself. Find one bounded real problem, reproduce it, fix it in an isolated candidate, add/run a regression, independently verify it, save a reusable lesson, restart, then attempt a new analogous task."

Cloud CI cannot claim this live model gate.

Tonight, prove the deterministic plumbing with fixtures/MOCK_MODEL.

Tomorrow, owner hardware proves the REAL_MODEL intelligence path.

Statuses remain distinct:

- `SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT`
- `SELF_REPAIR_SINGLE_CYCLE_PASS`
- `SELF_REPAIR_3_CYCLE_PASS`
- `TRANSFER_MEASURED_GAIN`
- `24H_SOAK_PASS`
- `48H_SOAK_PASS`
- `WEEK_MODE_READY`
- `REVENUE_CAPABLE_PILOT`

Do not skip levels by prose.

---

## 9. CLAUDE TEACHER MODE

Tomorrow Claude Code should control and audit Bossman through Terminal 1.2 instead of spending the experiment clicking GUI controls.

Teacher behavior:

```
LOCAL BOSSMAN ATTEMPTS
→ tests/evidence
→ Claude observes
→ minimal hint if required
→ local Bossman retries
→ independent verifier decides
→ verified lesson may be stored
```

Teacher levels:

- L0 no help
- L1 violated invariant
- L2 root-cause class
- L3 diagnostic strategy
- L4 architecture approach
- L5 teacher patch

L5 is never counted as student success.

The terminal is a control surface only; it must not alter the semantics of training or permissions.

---

## 10. MODEL / SKILL WORK TONIGHT

Do not spend the night downloading huge weights.

Do not make optional challenger models block tomorrow's baseline.

Prepare verified manifests/profiles for already selected candidates.

Keep current known-good baseline runnable.

Model promotion requires real owner-hardware evidence.

Skills:

- may be CANDIDATE;
- cannot grant new permissions;
- need source/revision/license/test status;
- must be selectively loaded, not dumped into every context.

---

## 11. CI CLOSURE — NO NEW FEATURES AFTER FREEZE

Once the final functional delta is in, enter freeze.

Then only:

- reproduce red;
- fix red;
- rerun;
- package;
- certify;
- document exact status.

Do not add new experimental features after freeze.

Mandatory checks include the current repository-required workflows plus:

- root tests/hygiene;
- bossman-core;
- command-center core-runtime on Linux and Windows;
- Terminal tests;
- coding path tests;
- evolution/lab/verifier tests;
- package/bundle tests;
- secret scan;
- `git diff --check`;
- skips registry consistency.

Do not weaken tests to obtain green.

Known OWNER_REQUIRED hardware measurements stay OWNER_REQUIRED.

---

## 12. BUILD EXACT-SHA ARTIFACT

Freeze exactly one SHA:

`FINAL_CANDIDATE_SHA`

Build Windows ZIP from those exact bytes.

Record:

- branch;
- full SHA;
- artifact name;
- artifact URL/ID;
- artifact SHA-256;
- artifact size;
- workflow run IDs;
- test summary;
- remaining owner-required checks.

Re-run the installed artifact outside the checkout.

A docs commit after certification creates a new SHA and does not inherit the old certificate.

---

## 13. PROMOTE TO main ONLY AFTER GATES

Owner wants tomorrow's working prototype in `main`.

At the time of this prompt, cloud convergence was ahead of `main` with no divergence. Re-check immediately before promotion.

Promotion rule:

### If main is still an ancestor

Use a normal fast-forward of `main` to the exact certified candidate SHA.

### If main has new commits

Merge current `main` into convergence first.

Resolve by meaning.

Run mandatory CI/package checks again.

Only then merge/advance `main`.

### Never

- force-push main;
- reset main to a different history;
- squash away useful provenance;
- push a red SHA to main and promise to fix it tomorrow.

Before moving main, create a durable backup ref/tag pointing to the previous main SHA, e.g. an owner-approved archival ref with date/SHA, without deleting anything.

After promotion, verify:

```
main SHA == FINAL_CANDIDATE_SHA
```

or, if a merge commit was necessarily created, certify that exact merge SHA before calling main ready.

---

## 14. DEFINITION OF "WORKING TOMORROW"

Tomorrow's prototype is READY FOR OWNER RUN only if the owner can:

1. obtain the exact Windows artifact;
2. unpack it outside the repo;
3. start Bossman;
4. open `Bossman-CLI.cmd`;
5. talk to the same Bossman used by Dashboard/Telegram;
6. see current models/skills/memory/tasks;
7. launch a safe task;
8. see tools/events/result;
9. STOP it;
10. restart and reconnect;
11. invoke the coding/self-repair path;
12. have Claude Code observe it via JSONL;
13. receive an honest result without mock/intelligence confusion.

The actual first REAL_MODEL self-repair may be tomorrow's experiment. The product must be ready to run that experiment.

---

## 15. OWNER TOMORROW RUNBOOK

Prepare one short owner document with the actual final commands, no placeholders.

It should start approximately:

```
1. verify artifact SHA-256
2. unpack
3. Start-Bossman.cmd
4. Bossman-CLI.cmd
5. bossman.cmd status --json
6. select/verify local model
7. run baseline practical task
8. run first self-repair task
9. Claude teacher attaches through stream-json
10. verifier checks candidate
11. restart
12. transfer task
```

Do not make the owner hunt through old docs.

---

## 16. FINAL REPORT FORMAT

At completion print exactly:

```
FINAL_BRANCH=
FINAL_CANDIDATE_SHA=
MAIN_BEFORE=
MAIN_AFTER=
MAIN_PROMOTED=YES/NO
WINDOWS_ARTIFACT=
WINDOWS_SHA256=
CI_STATUS=
TERMINAL_1_2=
EVOLUTION_1_1=
SELF_REPAIR_PLUMBING=
REAL_MODEL_SELF_REPAIR=OWNER_REQUIRED/PASS/FAIL
OPEN_P0=
OPEN_P1=
OWNER_REQUIRED=
START_TOMORROW=
```

Then a short section:

### What changed tonight

### What is proven

### What is not proven

### Exact 3 commands the owner runs first

---

## 17. FINAL PRIORITY

The night is successful if tomorrow we have **one coherent Bossman**, not the largest number of new features.

Priority order:

1. convergence correctness;
2. critical bug fixes;
3. Terminal 1.2 usability;
4. self-improvement plumbing;
5. Windows installed-product truth;
6. CI/exact-SHA artifact;
7. promotion to main;
8. clean owner handoff.

North Star remains:

> Local Bossman should increasingly improve Bossman, prove the improvement, retain reusable verified lessons, and apply them to useful work while Claude moves from primary fixer to teacher/auditor.

Do the work. Push after each coherent completed step. Preserve other agents' work. Stop expanding scope once the final candidate enters certification.
