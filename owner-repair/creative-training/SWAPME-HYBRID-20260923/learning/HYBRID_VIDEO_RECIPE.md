# HYBRID_VIDEO_RECIPE — short vertical ad, cloud + local (verified on SwapMe 15 s, 2026-09-23)

WEIGHTS_UNCHANGED — this is a retrievable recipe (Bossman memory), not fine-tuning.
Authors: CLAUDE_TEACHER (decisions, prompts), CLOUD_PROVIDER (Seedance 2.5), LOCAL_MODEL (Wan2.2 TI2V-5B), DETERMINISTIC_TOOL (ffmpeg/PIL).

## Decision rules (why cloud / why local)
1. Walking / entering / camera dolly through a complex city or a new interior → CLOUD (Seedance 2.5, 5 s, 720p 9:16).
2. Character acts with objects (sits, slides a coin, physics) → CLOUD.
3. Close-up continuing the previous shot (face, glasses, hand, product) → LOCAL I2V from the cloud shot's LAST frame.
4. Near-static hero / product insert under the end card (coin spins, slow push-in) → LOCAL I2V.
5. Typography, logo, CTA → compositor (PIL/ffmpeg) — never diffusion.
6. A weak local attempt → change the task first (I2V from a better frame, less motion, shorter clip, more steps) before escalating to cloud.
7. No blind paid retries. Accept a good cloud result; do not regenerate for micro-gains.

## Selected presets (measured on Ryzen AI Max+ 395 / Radeon 8060S, Smart App Control On)
- CLOUD: `openrouter:bytedance/seedance-2.5`, duration 5, 720p, 9:16, generate_audio true, 1–2 reference stills (role `reference`). Real price: $1.165 per 5 s shot (usage.cost). Studio queue: ~2.5 min per shot.
- LOCAL: `sdcpp:wan2.2-ti2v-5b`, 640x1152, 65 frames @24 fps (2.7 s), 24 steps, cfg 5.0, seed fixed, role `start` = adjacent cloud shot's last frame resized 648x1152 → crop 640x1152.
  Bench: 24 steps costs only +11 % time vs 16 but follows the prompt better. Portrait width < 640 is refused by the catalog.

## Edit pattern (15.00 s, 24 fps, 1080x1920)
S1 cloud 0–5 → L1 local 5–7.5 (seamless: starts on S1 last frame, drop its first frame) → S2 cloud 7.5–12.5 (0.16 s light-flash on a beat)
→ L2 local 12.5–15 (seamless from S2 last frame) + end card from 12.55 s.
Common grade on all shots (eq contrast 1.04 / saturation 1.06, vignette) hides the cloud/local difference.
Sound: procedural 120 BPM (cuts on beats 5.0 / 7.5 / 12.5), whooshes on cuts, riser → impact on the logo, cloud ambience under cloud shots, loudnorm −14 LUFS.

## Budget formula
cloud_cost = shots × $1.165 (5 s each). 15 s cloud-only ≈ 3 × $1.165 = $3.50. Hybrid (2 cloud + 2 local) = $2.33 → −33 % cloud cost, +local GPU time.

## Pitfalls found (fixed in Bossman, branch fix/studio-seedance-durations-20260923)
- Studio catalog allowed only 15 s for Seedance 2.5 → now 4–15 s (6a2b5907).
- Studio fetched the paid OpenRouter video without the key → 401 → `failed: malformed` (money spent, video lost) → fixed (99970958).
