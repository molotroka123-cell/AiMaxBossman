# Bossman-only Owner Simulation — GREEN Contract

## Owner expectation

The owner should be able to:
1. start the mission in Bossman CMD/UX;
2. leave;
3. return after the four-hour window;
4. launch the delivered build;
5. play it without first debugging Bossman's work.

## Single execution boundary

After mission start, all normal development work goes through the **same Bossman
backend via CMD or UX**.

Allowed:
- Bossman tools invoking Git, Godot CLI/editor automation, filesystem, browser,
  model gateway and tests;
- Jev scheduling through Bossman;
- agents spawned/managed by Bossman;
- Aster reading evidence through Bossman;
- Claude teacher through an already authorized Bossman integration;
- local/free model calls routed and logged by Bossman.

Not GREEN:
- human directly edits code;
- human fixes Godot scene/inspector state;
- separate Claude/Codex session edits the project outside Bossman;
- hidden script mutates project state without Bossman task/evidence identity;
- manual copying of a known working game implementation;
- tests are weakened/deleted to turn green.

## Zero-known-bug definition

No engineering process can prove that software has no possible undiscovered
bug.

For this benchmark, GREEN means:
- all frozen acceptance tests pass;
- all defects discovered by QA/Aster/owner-simulation are resolved or explicitly
  removed from the frozen scope before feature freeze;
- no open P0/P1/P2 defect exists inside the declared benchmark scope;
- final clean-install/restart owner-equivalent run finds no known defect;
- no false PASS;
- independent verifier accepts the exact final build hash.

## Simulated owner behavior

The Owner Emulator must:
- launch from the same deliverable the owner receives;
- start a new world;
- walk/run/look/jump;
- collide with terrain;
- break blocks;
- place blocks;
- change hotbar selection;
- intentionally try invalid placement;
- save;
- quit;
- relaunch;
- confirm persisted modifications;
- create/load a second world or reset world if implemented;
- stress movement at world/chunk boundaries;
- test rapid break/place;
- test pause/menu/exit;
- observe UI readability at target resolution;
- run for a bounded soak period;
- capture screenshots/video/logs.

Every step records expected vs actual post-state.

## Cost-quality objective

Primary optimization target:

`maximize verified owner-equivalent quality subject to four-hour deadline and
owner cost policy`.

Secondary:
`minimize total incremental cost`.

Decision order:
1. use already-loaded/local verified model when adequate;
2. use legitimate free capacity when it materially helps;
3. use already-funded/authorized teacher path;
4. paid incremental route only with existing authority;
5. never spend merely to make a benchmark number look better.

Record:
- local model time;
- free cloud requests;
- Claude teacher interventions;
- incremental USD;
- time per verified feature;
- retries;
- total owner interventions.

GREEN target for this benchmark: **0 owner interventions after start**.

## Learning requirement

A run is not full GREEN unless learning artifacts are produced.

For each meaningful failure/fix:
- symptom;
- reproduced evidence;
- root cause;
- repair;
- regression;
- applicability;
- counterexample/limits;
- producer;
- verifier;
- outcome.

Aster proposes the generalized lesson.
Verifier approves/rejects.
Skill Compiler decides whether to make:
- lesson;
- regression;
- reusable skill;
- benchmark case;
- training candidate.

After the final build, at least one relevant learned artifact must survive a
Bossman restart and be retrievable by the proper agent.

## Aster final question

Aster must answer:

> If the owner gives Bossman a similar game task tomorrow, what exactly should
> be cheaper, faster or more reliable because of today's run?

A final "nothing learned" is allowed only if supported by evidence. Fabricated
learning fails the benchmark.
