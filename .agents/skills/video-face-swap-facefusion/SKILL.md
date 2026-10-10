---
name: video-face-swap-facefusion
description: Swap a consenting person's face into a video locally with FaceFusion through Bossman (quality preset, lossless parallel chunks, VFR fix, pose gate, source kept outside the face, original framing and audio), prove it with the animation gate, and deliver it to the owner's pult.
compatibility: BOSSMAN (Windows, Radeon 8060S, FaceFusion ONNX DirectML, ffmpeg, MediaPipe gate venv)
metadata:
  owner: bossman
  version: "1.0"
  learned_from: owner session 2026-10-10 (5 owner clips, Gate 0/1, two-person clip, Genjutsu comparison)
---

# Video face swap (FaceFusion, local, free)

## Consent and scope (before anything)

Only the owner, or people the owner says agreed (owner-supplied photos). No intimate content of real people, no
deception (results are never presented as genuine footage), personal media never leaves the PC, results go only to
the owner's pult. Genjutsu/Higgsfield and any paid API: never without the owner's explicit yes ("он платный").

## Recipe (executable)

1. **References** (`gf-refs-all/pick.py` pattern): arcface consistency matrix, drop blurry/outlier photos (cos < ~0.5 to the
   median), keep big sharp frontal faces; frames from the person's own video circles are excellent (consistency 0.87-0.92).
   Max 5 photos (`faceswap.MAX_SOURCES`). Joint photos: crop to one person or FaceFusion averages two faces.
2. **Run through Bossman** (UI «Замена лица в видео» / `POST /api/direct-gen/swap-jobs`, preset `quality`), or the same
   steps by script (`scratchpad/run_final.py` pattern = `DirectGenService._run_swap`):
   - VFR source (phones, screen recordings) -> `normalize_cfr` at its own average rate (else chunks drift from audio);
   - `plan_chunks` + `cut_lossless` (H.264 -qp 0) -> 2 FaceFusion processes, each with its OWN `--temp-path` and
     `--jobs-path` (shared jobs dir: the 2nd process exits 1 silently); 64.7 s -> 40.5 s, bit-identical frames;
   - `join` (concat copy) -> **pose gate** -> `finish` (H.264 crf 16, source audio, `-fps_mode passthrough`,
     `-t` = video length, never `-shortest`).
3. Preset `quality` = hyperswap_1a_256, pixel boost 1024, mask box+occlusion (xseg_1), gfpgan_1.4 blend 50,
   `--execution-thread-count 1` (several models x threads -> segfault 139 on DirectML; xseg_2 crashes). 1.2-1.7 frame/s.
4. Only one person should change: `--face-selector-mode reference --reference-frame-number 0 --reference-face-distance 0.5`
   and ONE process (the reference is taken from the first frame of each process's own input). Two different people
   (owner + partner): split by scenes, `many` per scene with that person's photos — reference mode put the wrong face
   on the other person.
5. Never refit/stretch to 16:9 (owner: «растянул коряво»): keep the source resolution, framing, timing and audio.
6. Pult: `/tmp/send_vid2.py` pattern (owner only); files > 49 MB are refused -> re-encode crf 20. Caption with honest
   numbers (identity before/after, frames, seconds, resolution, what was gated).

## Pose gate + source outside the face (`bcc/direct_gen/pose_gate_worker.py`)

- A bowed head stays a confident detection (0.78-0.80) and gets a frontal face pasted on the crown (eyes on the
  forehead). Pitch = (nose_y - eyes_y)/(chin_y - eyes_y): bowed 0.54-0.62, normal 0.32-0.46. alpha fades the swap out over
  0.50-0.56 on the source face.
- FaceFusion's video merge re-ranges colours of the WHOLE frame (bt709 tv: mean 45.39 -> 42.93). The worker restores
  everything outside the face boxes padded by 0.6 x side from the source, linear seam, RGB-lossless intermediate
  (`libx264rgb -qp 0`; yuv420p -qp 0 is NOT RGB-lossless).

## Proof (Gate 0 / Gate 1) — `tools/video_gate/`

- Thresholds are fixed in `docs/owner/VIDEO_PIPELINE_STAGES_20261010.md` BEFORE running (commit first). Never move them
  after seeing a result; if a metric itself is wrong, fix the method, version it (`metric_version`), record the failed
  version and rerun everything.
- `gate0_passthrough.py <video> <out>` = the pipeline with an identity effect; `animation_gate.py --gate 0|1` measures
  frames/alignment, pixels outside the face mask, 68 face landmarks, MediaPipe body pose, Farneback flow, jitter,
  identity; writes report.json, side_by_side.mp4, contact_sheet.jpg. Gate venv: `C:\Users\asd\Bossman\video-gate-venv`
  (mediapipe 1.1 = Tasks API only, model `models/pose_landmarker_full.task`).
- Frame path proof (10.10): FaceFusion job API with one step per PNG frame and ALL photos per step
  (`batch-run` is a source x target cartesian product — every frame gets ONE photo). 90/90 frames: 0 changed pixels
  outside the face, landmark NME 0.019, identity 0.794 (std 0.025), source 0.154.
- Telecined sources have near-identical neighbour frames (MSE 0.01 vs median 190): a lossy result ties with the twin —
  that is not a shift (metric v2). Two people / blur: match the result's face/body to the same source person (IoU),
  never "largest in each clip".

## Limits learned

- Identity is capped by the source: on a 480p / beauty-filter clip 3 photos, 1 photo or 13 photos all gave 0.63.
- Genjutsu object swap changes clothes/hair well but the face weakly (0.16 -> 0.64 only after FaceFusion on top).
- Clothes / hair / whole person need a generative model: see `genjutsu-character-swap` (Wan2.1 VACE on Vulkan) and the
  Stage 2 notes in the stages doc; Wan2.2-Animate on ROCm Windows crashes (HIP launch failure, suspected 2 s TDR).
