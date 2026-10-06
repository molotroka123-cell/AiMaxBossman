# BossBlocks-001 — Four-Hour Autonomous Game Benchmark

## Purpose

First serious BossNet Game Studio exam.

One owner instruction through Bossman -> four hours autonomous work -> packaged
original voxel sandbox -> owner plays result.

This tests Bossman as a developer, not merely the intelligence of one model.

## Engine

Primary target:
- Godot 4.7.2 stable;
- Voxel Tools 1.7 if pinned compatible binary/source is available and preflight
  succeeds quickly.

Why:
- open-source/lightweight editor/runtime;
- command-line project/build automation;
- deterministic scripting-friendly workflow;
- Voxel Tools provides Minecraft-style voxel/block terrain capabilities.

### Fallback
If Voxel Tools blocks the critical path for >15 minutes due to binary/version
compatibility, Jev must switch to a bounded pure-Godot voxel/chunk implementation
rather than spending the four-hour test compiling infrastructure indefinitely.

Do not download a complete Minecraft clone.

## Originality/license

Minecraft is only the genre/scale reference.

Do not copy:
- Minecraft source/code;
- game assets/textures;
- sounds/music;
- logo/name/branding;
- maps/world seeds from proprietary content.

Use simple original/procedural materials and shapes.

## Frozen v1 scope

Required:

### Core
- first-person mouse look;
- WASD movement;
- jump;
- gravity/collision;
- generated block/voxel terrain;
- visible ground/sky;
- raycast target indication;
- break block;
- place block;
- at least 3 block/material types;
- hotbar or equivalent selection;
- prevent invalid placement inside player;
- pause/exit;
- save changed blocks/world state;
- quit/relaunch/load persistence.

### Product quality
- clear launch path;
- target Windows build if exporter available, otherwise documented owner-platform
  build plus runnable editor/project fallback;
- no critical console/script errors;
- acceptable control latency;
- no obvious stuck spawn;
- no immediate fall-through-world;
- no known benchmark-scope P0/P1/P2 at final freeze.

### Nice-to-have, only after required scope is green
- day/night;
- simple inventory count;
- procedural trees;
- basic ambient sound;
- block particles;
- settings menu.

No animals, crafting, multiplayer, enemies or infinite polished world in this
first benchmark unless all required scope is already green with time remaining.

## Timebox

### T-00:00 to 00:15 — preflight + self-bootstrap
Bossman:
- creates mission/run identity;
- calls the pinned game-bootstrap plan;
- downloads/verifies/unpacks required portable tooling through Bossman only;
- sends Telegram milestone signals without per-edit spam;
- verifies Godot/runtime;
- verifies model fleet;
- freezes acceptance;
- initializes repo/project;
- runs empty-project build/launch smoke.

### 00:15 to 00:45 — vertical skeleton
Goal:
- project launches;
- FPS controller;
- camera;
- basic flat/block world;
- collision.

Aster checkpoint at 00:30.

### 00:45 to 01:30 — voxel interaction
- generated terrain/chunks;
- block targeting;
- break/place;
- material selection.

Milestone: player can modify world.

Aster checkpoints 01:00/01:30.

### 01:30 to 02:15 — persistence/product shell
- hotbar/UI;
- save changed state;
- quit;
- relaunch;
- load;
- pause/exit.

Milestone: persistence verified.

### 02:15 to 02:45 — feature freeze
No new optional feature after 02:30 unless zero open required-scope defects.

From 02:30 onward default work is tests, UX, performance, bugs and packaging.

### 02:45 to 03:30 — adversarial owner simulation
Owner Emulator runs all acceptance sequences.
Aster finds false-PASS risk and edge cases.
Workers fix discovered defects.
Every fix adds/reuses a regression.

### 03:30 to 03:50 — clean final acceptance
- clean restart;
- exact build/export;
- Owner Emulator fresh run;
- persistence test;
- soak;
- performance/log check;
- Aster final audit.

No speculative refactor.

### 03:50 to 04:00 — handoff
Freeze exact final SHA/build hash.
Write lessons/skills/evidence.
Produce one owner launch instruction.
Stop development at four hours.

## Bossman-only gate

All execution after initial owner instruction must be attributable to Bossman CMD
or UX task IDs.

Audit checks:
- git commits correlate with Bossman tasks;
- shell/Godot commands appear in Bossman evidence;
- model calls appear in routing ledger;
- no unexplained file mutations;
- no separate manual agent editing session.

Violation -> `BOSSMAN_BOUNDARY_FAIL`, not GREEN.

## Models / limit saver

Every coding packet uses `coding_limit_saver_v16`.

Default author priority:
1. local verified worker;
2. local specialist/challenger;
3. legitimate free cloud worker where data class permits;
4. bounded GLM-5.3-Flash coding escalation;
5. no other code writer unless owner changes the benchmark contract.

Claude can remain a teacher/advisor through an authorized Bossman path, but the
normal code author for this benchmark remains LOCAL/FREE/GLM53_FLASH.

Aster is AUDIT_ONLY and `ASTER_CODE_WRITES` must equal zero.

Jev tracks verified quality/cost/latency/context, not cheapest token alone.
Default cheap-attempt and GLM caps come from the Coding Limit Saver contract.

## Automated acceptance matrix

### A — boot/build
A1 project imports.
A2 scripts parse.
A3 main scene starts.
A4 no critical error in initial log.
A5 deliverable launches from documented command.

### B — player
B1 forward/back/strafe.
B2 mouse look.
B3 jump returns to ground.
B4 collision blocks passage through solid terrain.
B5 spawn is safe.

### C — world
C1 terrain has multiple blocks.
C2 at least three selectable types.
C3 target block can be identified.
C4 break changes actual world state.
C5 place changes actual world state.
C6 invalid player-overlap placement refused.

### D — persistence
D1 modify known coordinate.
D2 save/quit.
D3 kill/restart process.
D4 reopen same world.
D5 exact modification remains.

### E — UX
E1 hotbar/selection readable.
E2 pause/exit works.
E3 no required action needs developer console.
E4 controls are documented in-game or start screen.

### F — adversarial
F1 rapid break/place bounded.
F2 world/chunk boundary movement.
F3 repeated save.
F4 relaunch twice.
F5 invalid target/place does not crash.
F6 fresh world/start state still works.

### G — learning
G1 at least one discovered issue has reproduced evidence.
G2 verified lesson survives restart.
G3 relevant agent can retrieve it.
G4 Aster final improvement report exists.
G5 no unverified teacher text is marked learned.

## Owner-equivalent GREEN

GREEN only if:
- Bossman boundary passes;
- all required A-F gates pass;
- G learning gates pass;
- 0 owner interventions after mission start;
- zero known in-scope P0/P1/P2;
- final Owner Emulator pass on exact build;
- Aster FINAL_ACCEPT;
- no incremental unauthorized spend;
- final artifact hash/commit recorded.

Otherwise final result is PARTIAL/FAIL with exact blockers.

## Benchmark metrics

Record:
- four-hour wall time;
- bootstrap time and fallback count;
- verified tool/download hashes;
- Telegram milestone count and owner approvals;
- feature completion time;
- local/free/GLM/Claude request counts;
- ASTER_CODE_WRITES (must be 0);
- coding cache hits;
- context tokens avoided/saved where measurable;
- incremental USD;
- total model tokens if available;
- owner interventions;
- number of Aster recommendations;
- recommendations verified/promoted;
- bugs found before/after feature freeze;
- retries;
- context tokens by agent;
- build/export time;
- owner-simulation failures;
- final FPS/frame time if measurable;
- lessons/skills created.

## Success beyond the game

The most valuable output is not only BossBlocks.

The run should make tomorrow's second game cheaper because Bossman retains:
- Godot bootstrap skill;
- voxel interaction skill;
- build/export skill;
- owner simulation suite;
- failure cases;
- Aster improvements;
- model/agent performance statistics.
