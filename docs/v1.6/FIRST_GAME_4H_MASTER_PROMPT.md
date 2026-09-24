# MASTER PROMPT — BOSSMAN FIRST GAME / BOSSBLOCKS-001 / 4 HOURS

Repository: `molotroka123-cell/AiMaxBossman`.
BossNet lane: `feat/bossman-1.6-bossnet-foundation-20260925`.

## Mission

Through the **real Bossman CMD/UX and same backend only**, build an original
Minecraft-like voxel sandbox in at most four hours, autonomously test/fix it,
package it and leave a build the owner can immediately play.

The owner should not edit code, scenes or configuration during the run.

Read first:
- `docs/v1.6/OWNER_SIMULATION_GREEN_CONTRACT.md`
- `docs/v1.6/JEV_ASTER_GAME_RUN_CONTRACT.md`
- `docs/v1.6/FIRST_GAME_4H_BENCHMARK.md`
- `docs/v1.6/GAME_STUDIO_IDEAS.md`
- `docs/v1.6/CODING_LIMIT_SAVER.md`

Do not plan a new scope. Execute the frozen benchmark.

## Engine

Use Godot 4.7.2 stable.

Use Voxel Tools 1.7 only if exact compatibility/preflight is green quickly.
If Voxel Tools blocks the critical path for >15 minutes, Jev switches to the
bounded pure-Godot fallback.

Do not use a complete Minecraft clone.

## Control hierarchy

OWNER -> Bossman CMD/UX -> Jev -> Bossman agents/tools/models.

Aster observes independently through Bossman.

All normal file edits, shell commands, Git operations, Godot invocations,
model calls and tests must carry Bossman mission/task/evidence identity.

Anything materially edited outside Bossman invalidates GREEN.

## Model / quota policy

Every code-authoring packet MUST call the Bossman Coding Limit Saver policy.

Allowed code writers for this benchmark:
- LOCAL;
- FREE;
- GLM53_FLASH (`z-ai/glm-5.3-flash`, when the configured route is available and
  policy/budget permits).

Use current GREEN local models first and legitimate free cloud workers when data
policy permits. After bounded cheap failures, GLM-5.3-Flash may author the hard
fix within its call/budget cap.

**Aster never writes product code. ASTER_CODE_WRITES=0 is a GREEN invariant.**
Aster receives compact evidence/checkpoint packets and spends its capacity on
audit + generalized improvements.

Claude is a teacher/escalation path through Bossman only, not the normal coding
worker. Any Claude advice/result still needs independent verification.

Reuse verified skills/cache before another inference. Keep scoped context small;
do not dump the full repo into every worker.

No new paid spend unless current owner policy already grants it.

## Jev

Jev:
- maintains critical-path DAG;
- schedules workers;
- prevents duplicate work;
- enforces context budgets;
- selects cheapest adequate route;
- triggers executable milestone checks;
- freezes optional feature work by 02:30;
- stops the run at 04:00.

Jev cannot self-certify.

## Aster

Every 30 minutes Aster writes:
1. status;
2. known defects;
3. false-PASS risks;
4. duplicated/wasted work;
5. context/resource waste;
6. cheaper/better alternative;
7. a generalized improvement proposal.

Send the proposal to relevant agents + learning compiler.
It remains PROPOSED until verifier evidence accepts it.

Aster does not silently patch normal code.

## Worker roles

Spawn only as needed:
- Game Director;
- Godot/Gameplay Coder;
- Voxel/World Coder;
- UI/Persistence Worker;
- Build/Performance Worker;
- QA Player;
- Independent Verifier.

Do not run a giant council on trivial steps.

## Required game scope

- first-person movement/mouse/jump/gravity/collision;
- generated block world;
- at least 3 block types;
- ray target;
- break/place;
- selectable hotbar/type;
- refuse block placement inside player;
- pause/exit;
- save;
- full process restart;
- load changed world state;
- final playable build/project.

Nice-to-have features are forbidden while any required gate is red.

## Owner Emulator

Before final handoff, Bossman must imitate the owner using the final deliverable:
launch -> new world -> move/look/jump -> break -> select -> place -> invalid
place -> save -> quit -> process restart -> load -> verify modification ->
boundary/rapid interaction -> pause/exit -> bounded soak.

Do not trust internal model statements.

## Bug policy

Every discovered in-scope bug:
REPRODUCE -> CLASSIFY -> FIX -> REGRESSION -> REPLAY -> VERIFIER.

Do not call GREEN with an open known P0/P1/P2 in frozen scope.

Do not weaken a test to make it pass.

## Learning policy

For each useful fix, record symptom/root cause/repair/regression/applicability.

Aster proposes generalized lesson.
Verifier checks.
Skill Compiler may promote lesson/skill/benchmark candidate.

At least one verified learning artifact must survive Bossman restart and be
retrieved by the relevant agent before final GREEN.

## Cost policy

Optimize verified owner-equivalent result, then minimize incremental cost.

Report local/free/Claude usage and actual incremental USD.
Unauthorized incremental spend = FAIL.

## Final output

Create:
`docs/v1.6/runs/BOSSBLOCKS-001-RESULT.md`

Include:

```
RUN_ID=
START_SHA=
FINAL_SHA=
FINAL_BUILD_HASH=
BOSSMAN_ONLY_BOUNDARY=
ENGINE=
VOXEL_BACKEND=
WALL_TIME=
OWNER_INTERVENTIONS=
LOCAL_MODELS=
FREE_MODELS=
GLM53_CODE_CALLS=
CLAUDE_ESCALATIONS=
ASTER_CODE_WRITES=
CODING_CACHE_HITS=
CONTEXT_TOKENS_SAVED=
INCREMENTAL_USD=
REQUIRED_GATES=
OWNER_EMULATOR=
ASTER_VERDICT=
OPEN_P0=
OPEN_P1=
OPEN_P2=
LESSONS_VERIFIED=
SKILLS_PROMOTED=
KNOWN_LIMITATIONS=
FINAL_STATUS=
OWNER_LAUNCH_COMMAND=
```

FINAL_STATUS can be GREEN only when the exact contract says so.

At 04:00 stop. A truthful PARTIAL beats a fake GREEN.
