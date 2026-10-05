---
name: genjutsu-character-swap
description: Replace the actors of a short video clip with consenting people from reference photos, locally (Wan2.1 VACE via stable-diffusion.cpp), keeping the background, camera and music. Multi-shot, smart region generation, parallel segments.
compatibility: BOSSMAN (Windows, Vulkan sd-cli, onnxruntime prep models)
metadata:
  owner: bossman
  version: "1.0"
  learned_from: owner test 2026-10-05 (13 s clip, 3 shots, two actors -> owner and partner)
---

# Genjutsu character swap (local)

## Consent gate (before anything)

Only the owner's own face/body or people who agreed (the owner states it, e.g. joint selfies sent by the owner).
Refuse: sexualised or humiliating edits of real people, minors, putting a real person into a scene to deceive.
Replacing a public figure *out* of a meme with consenting people is fine; inserting someone into it is not.

## Recipe (executable)

1. Unload the machine: `owner_one_bossman.py stop`, `ollama stop <each model>`, stop llama-server. Need >= 40 GB free RAM
   per parallel segment (the guard kills sd-cli below 16 GB free).
2. References: one clean cut-out per person on white, 480x832 (`refs_manual.py` pattern): SAM2 mask of the person
   **intersected with a hand-drawn polygon**. An automatic cut of a selfie with two people leaks the other person
   into the reference and the model then paints both.
3. Prompts: one per shot, describing the *new* person (hair, beard, clothes) and the shot (bar, stage, light).
4. Dry run, then look at `plan_preview.png` and the trace before spending GPU time:
   ```
   python tools/genjutsu/film.py --src clip.mov --job JOB --seconds 13 \
     --shot-refs ref_a.png,ref_b.png,ref_b.png --shot-targets largest,center,largest \
     --shot-prompts prompts.json --plan-only
   ```
   Check: every shot replaces the right person; the grey hole covers only that person.
5. Real run: same command with `--parallel 1 --steps 20` instead of `--plan-only`. Re-running skips finished segments.
   Measured 2026-10-05: `--parallel 2` on the single Radeon 8060S (Vulkan) -> "device lost on Vulkan0" during
   VAE encode, killing every concurrent sd-cli at once. One GPU = one generation; parallelism only across GPUs.
   Outputs: `film_24fps.mp4` (music kept), `film_16fps.mp4`, `review_source_vs_result.mp4`, `trace.jsonl` (timings).
6. Send result + review video to the owner's пульт; record timings and defects in the job's trace.

## Shot targets (what went wrong first time and why)

- `largest` = the biggest person at the shot start, then tracked. Right for close-ups / medium shots.
- `center` = a confident (score >= 0.6) whole person near the frame centre. Use for over-the-shoulder shots:
  `largest` picked the blurry foreground shoulder; without the centre/score filter a spectator in the crowd
  was picked and the region grew to the full frame.
- Segments are clamped to their shot and only cover frames where the actor is visible
  (a segment that ran into the next shot pulled that shot's big mask in and disabled the crop).

## Smart generation

Close-up (actor box > 60% of the width) -> generate the full frame. Wide shot -> 9:16 crop around the actor
(min 30% of the width for context), upscaled to 480x832; everything outside the actor mask stays the
untouched source pixels, so background quality = source quality.

## Failure signs -> what to do

- Two people in the output / identity mix -> reference leaked a second person: recut with polygon (step 2).
- Coloured bones in output -> control had the skeleton; use the plain grey hole (default).
- OOM_GUARD in trace -> lower `--parallel` to 1 or free more RAM; keep `--vae-tiling`.
- Dotted mask edges -> Segmenter must upsample logits before thresholding (already in genjutsu.py).
- sd-cli refuses the model -> use official Comfy-Org safetensors, not community GGUF.
