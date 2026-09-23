# Game Studio — Next Reuse Target

Status: NEXT-STAGE SPEC, not tomorrow's first priority.

## Goal

From one Bossman request, create an original playable FPS vertical slice with a verified autonomous build/play/repair loop.

Quality reference may be modern AAA military/cinematic FPS, but do not copy protected Call of Duty maps, characters, missions, audio, names or assets.

## Reuse the WebDesigner architecture

WebDesigner:
`generate -> browser render -> visual critic -> functional verifier -> patch -> rerender`

Game Studio:
`generate -> Unreal build -> autonomous playtest -> visual/game-state verifier -> patch -> rebuild -> replay`

The same components should be reusable:
- role/model router;
- capability registry;
- verifier contracts;
- evidence manifests;
- visual critic;
- dataset factory;
- preference/repair training;
- cost/resource leases;
- restart/recovery.

## Engine

Primary: Unreal Engine 5.

Automation order:
1. official Unreal Python/editor scripting/commandlets;
2. reviewed Blueprint/C++ automation;
3. reviewed Unreal MCP/editor bridge only where it adds value.

Candidate discovery includes public Unreal-MCP/editor-control projects, but every candidate needs exact SHA, license, security review and bounded adapter.

## Agent roles

- Game Director;
- Level Designer;
- Gameplay Engineer;
- NPC/Enemy AI;
- Asset/World;
- Cinematic/VFX/Audio;
- Build/Performance;
- QA Player;
- Independent Verifier.

## Ladder

### GS-0
Create/open project, change content, build packaged executable, relaunch.

### GS-1
5-minute graybox:
- movement;
- one weapon;
- one enemy type;
- objective chain;
- death/restart;
- checkpoint;
- extraction;
- autonomous QA completion.

### GS-2
15-minute complete mission with multiple encounters and scripted events.

### GS-3
Lighting/environment/animation/VFX/audio/cinematic polish.

### GS-4
Bounded overnight autonomous production with restart/evidence/recovery.

## PASS

- clean project/package;
- executable launches;
- autonomous QA starts from beginning and completes critical path;
- objective state observed, not inferred from log text alone;
- checkpoint/death/restart works;
- no unknown-license required asset;
- no surprise paid action;
- package hash recorded;
- independent replay.

Visual quality is a scored review, not a fake binary PASS.

## Hardware coordination

On Ryzen AI Max+ 395 / 128 GB:
- unload unnecessary large LLM/video workers during Unreal shader/build/play workloads;
- use explicit GPU/RAM leases;
- measure editor RAM, inference RAM, cook peak and game FPS.

## Tomorrow

Do not derail Model Fleet/WebDesigner/H200 setup to build the full game.

Tomorrow's Game Studio work is limited to:
- preserve this spec;
- ensure new WebDesigner evaluator/training components are engine-agnostic;
- optionally prove GS-0 only if all higher-priority tracks are green.
