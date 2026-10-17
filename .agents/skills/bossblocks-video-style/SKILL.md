---
name: bossblocks-video-style
description: Turn a reference gameplay/engine video into a Godot 4 look for BossBlocks with a cheap model - keyframes, contact sheets, cached spec, SEARCH/REPLACE coder loop, visual verdicts. Includes the night floating-island style recipe and Godot pitfalls found on 2026-10-17.
metadata:
  owner: claude
  version: "1.0"
  source_run: creative-runs/BOSSBLOCKS-VIDEO-20261017
  game_branch: deepseek-video-20261017 (C:/Users/asd/Bossman/games/bossblocks-deepseek-20261017)
---

# bossblocks-video-style

Measured run 2026-10-17: DeepSeek V4.1 Flash (OpenRouter `deepseek/deepseek-v4.1-flash-1017`, accepts images)
analysed a 140 s voxel-engine video and wrote all game code for 5 visual tasks + 1 test fix. Total: 25 calls,
808k prompt tokens (300k cache hits), 245k output tokens (196k of them reasoning), $0.1248 billed (OpenRouter usage.cost). Claude only verified and gave visual feedback; zero hand edits.

## Pipeline (reusable for any reference video)
1. Frames: `ffmpeg -vf fps=2,scale=-2:512` + scene detect `select='gt(scene,0.20)'`; dedupe with a 16x16 dHash,
   keep a frame if Hamming > T vs the last kept AND > 0.8T vs every kept frame in the last 10 s; tune T to 30-45
   frames. Continuous orbit videos give almost no scene cuts (4 in 140 s) - dHash sampling is what works.
2. 3x3 contact sheets (tiles 288x512, label "Kxx t s" burned in) -> one vision call per sheet (~4.2k prompt tokens).
3. Prompt caching: put brief + game README + code facts + BOTH output schemas in ONE system message that is
   byte-identical in every call; only the sheet/descriptions go after it. Pin `provider.order` (DeepInfra) or
   OpenRouter spreads calls over providers and the cache misses. Cache hits are not guaranteed even when pinned.
4. Spec call: all per-frame JSON -> Russian Markdown spec with prioritised Godot tasks. Give >= 20k max_tokens:
   at 9k the model spent 5.4k on reasoning and truncated.
5. Coder loop: system = rules + edit format + spec; user = current main.gd + task. Harness applies
   SEARCH/REPLACE blocks verbatim, then: headless boot `--quit-after 120` scanning for SCRIPT ERROR / Parse Error /
   ERROR: (exit code 0 is not enough), full pytest, real Vulkan capture with a fixed verifier camera.
6. Visual verdict = measurements, not adjectives (mean luma of the island region vs the keyframe, sizes in
   blocks, hex colours). Every task needed 0-2 such rounds; tests alone passed renders that were 7x too dark.

## Style recipe (video "Voxel Engine" night floating island)
- Background `#0a1020`..`#0b1230`, 150-300 star billboards (one MultiMeshInstance3D, unshaded, emissive `#cfe8ff`).
- Island tops stay bright (mean luma ~85 on the video): keep the sun ABOVE the horizon at startup; night is a
  colour palette, not a sun below the world. Raise ambient so vertical faces are not black.
- Environment.glow_enabled with hdr threshold ~1.0; torches emission `#ffb347`, crystals emissive in own colour.
- Top HUD bar: dark translucent panel, "VOXEL ENGINE" + cube icon, StyleBoxFlat chips (1px border, radius,
  active tab cyan `#3fd0e0`), green FPS badge, FRAME counter.
- Pink `#ff9ad0` TorusMesh rings ~1.6-2 blocks across around objectives, unshaded + emissive, pulsing.
- Floating island: no boundary wall, noisy round silhouette, deep tapered underside (dirt band `#5a3a2a`
  then dark rock `#1d3a6b..#3a3a4a`), respawn when y < -30.

## Godot 4.7 pitfalls found in this run
- Default `day_time` that puts the DirectionalLight below the horizon (sun angle 270 deg) => top faces black
  while every test stays green. Always check a real render.
- Headless run printing `ERROR: N RID allocations ... leaked at exit` after adding materials/meshes = fail
  in our gate; free/own the resources via nodes in the tree.
- Headless tests that call `_save_world()` to a fixed `user://` path must delete that file BEFORE instancing
  the scene, otherwise state accumulates across runs (T4 ring test failed on the 4th run with
  "no crystals spawned" and the model wasted 3 attempts on the wrong code).
- `tests/owner_render.gd` asserts lit top faces do not clip: brightening by raising sun energy fails it;
  raise ambient / face shading instead.

## Prompt that worked (coder)
"You implement ONE task ... smallest safe diff ... EDIT FORMAT: FILE / <<<<<<< SEARCH / ======= / >>>>>>> REPLACE
... each task adds tests/owner_video_<task>.gd printing BOSSBLOCKS_VIDEO_<TASK>_PASS ... Everything must also work
under --headless" + the verifier camera definition, so the model knows how it will be judged.
