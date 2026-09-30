# Bossman 1.6 — Game Studio Ideas

Candidate extensions for 1.6. These are not implementation claims.

## 1. Agent League
Persistent game roles with measured performance:
GAME_DIRECTOR, LEVEL_DESIGNER, GAMEPLAY_PROGRAMMER, ENEMY_AI, VOXEL_WORLD,
UI_AUDIO, BUILD_PERFORMANCE, QA_PLAYER, FINAL_VERIFIER.

Store verified success, regressions introduced, first-pass rate,
time-to-verified-result, interventions, cost and failure classes by role/model.

## 2. Universal Evidence Kernel
No feature is complete from model text.

`claim -> evidence refs -> observer -> verifier -> artifact identity -> verdict`.

Examples:
- build -> export log + executable hash;
- break/place -> observed world-state delta;
- save -> process restart + loaded-state comparison;
- playable -> complete owner-equivalent QA trajectory.

## 3. Capability Dependency Graph
Compile a game request into reusable capabilities:
`engine bootstrap -> movement -> collision -> voxel world -> ray interaction ->
break/place -> hotbar -> save/load -> HUD -> export -> QA`.

Build only missing capabilities.

## 4. Artifact OS
Scenes, scripts, builds, saves, screenshots and tests receive immutable identities,
producer/model metadata, provenance, dependencies and verifier result.

## 5. Active Learning + Failure DNA
Normalize failures:
`compile, scene, physics, collision, world, persistence, UI, performance,
visual, context, schema, provider, false-pass`.

Verified failure -> fix -> regression -> replay can become a lesson/skill/training
candidate. Unverified explanations never become positive labels.

## 6. Counterfactual Replay
Replay recorded deterministic states with alternative models/settings without
touching the live project:
- chunk radius;
- movement settings;
- terrain seed;
- implementation strategy;
- agent/model route.

## 7. Simulation World for games
Before real execution, simulate performance envelopes, failure injection,
save/recovery, task dependencies and provider scheduling. Simulation informs a
decision but never proves the real build works.

## 8. Skill Compiler 2.0
Repeated verified game trajectories compile into compact typed skills:
- bootstrap Godot project;
- FPS controller;
- voxel ray interaction;
- deterministic save/load;
- Windows export;
- gameplay evidence capture.

A mature skill must reduce context/model work, not add prose.

## 9. Dynamic Ensemble Router
Cheapest capable worker first. Escalate only on evidence:
local -> legitimate free cloud -> stronger authorized teacher -> expensive
verifier/rented compute only if justified and allowed.

Optimize `verified_result / cost / time`, not tokens alone.

## 10. Model Foundry game specialists
Potential later adapters:
Bossman-Godot-Coder, Bossman-Unreal-Coder, Bossman-LevelDesigner,
Bossman-Game-QA, Bossman-Visual-Critic.

Only frozen holdout gain permits promotion.

## 11. Rental Work Packer
If owner later authorizes expensive GPUs, pre-stage code/model evaluation,
gameplay-video VLM analysis, LoRA, asset tagging and benchmark batches before
billing begins.

## 12. Causal Game Memory
Store candidate causal links:
`change -> measured outcome -> evidence -> confidence`.

Do not promote correlation to causation without controlled comparison.

## 13. Owner Simulation
Bossman should learn to imitate the owner's real acceptance behavior:
launch, click, play, break/place blocks, save, quit, reopen, inspect bad states,
try edge cases and reject anything that would annoy the owner.

The simulated owner is an acceptance agent, not a security authority.

## 14. Aster Improvement Broadcast
At each checkpoint Aster emits:
- what wasted time;
- what failed;
- what could be cheaper;
- what could be more reliable;
- what should become a skill/test;
- what context was unnecessary.

After verifier approval, Bossman distributes the generalized lesson to relevant
agents through the existing learning/skill path. Aster advice alone is not truth.

## 15. Game Control Room
UX eventually shows:
clock, task DAG, agents/models, context usage, build state, FPS/memory, failures,
Aster findings, lessons generated, artifact lineage, owner interventions and cost.

## 16. Quality/Cost Pareto
For every major implementation choice BossNet may compare candidates and retain
the best verified Pareto frontier:
- highest owner-equivalent quality;
- lowest total cost;
- shortest verified completion.

"Free but broken" loses to cheap and correct. "Expensive but marginally better"
loses when the improvement is not material.
