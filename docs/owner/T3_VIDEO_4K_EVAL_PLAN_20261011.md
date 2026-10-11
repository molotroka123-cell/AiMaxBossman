# T3-Video 4K: evaluation plan for Motion Studio (fixed BEFORE any run)

Date: 2026-10-11. Owner: video-model evaluator (role F). Status: Phase 1 (research, no GPU) done; Phase 2 (runs) not started.
Rule: nothing in section 4 (thresholds) may be edited after the first generation run. Any change is a new dated plan file.

## 1. Verified facts (public pages only)

| # | Fact | Value | Source |
|---|------|-------|--------|
| F1 | What it is | T3 = "Transform Trained Transformer": a retrofit of a pretrained full-attention video DiT. Multi-scale weight-sharing window attention + hierarchical blocking; core architecture unchanged. Fine-tuned on 42K UltraVideo clips. ICML 2026. | https://arxiv.org/abs/2512.13492 , https://zhangzjn.github.io/projects/T3-Video |
| F2 | Weights repo | `APRIL-AIGC/T3-Video`: `T3-Video-Wan2.1-T2V-1.3B.safetensors` 2.84 GB, `T3-Video-Wan2.2-T2V-5B.safetensors` 10 GB, repo total 12.8 GB | https://huggingface.co/APRIL-AIGC/T3-Video/tree/main |
| F3 | Base model | Wan-AI/Wan2.1-T2V-1.3B (the T3 file is a delta-fine-tuned DiT; base text encoder + VAE are still required) | https://huggingface.co/APRIL-AIGC/T3-Video |
| F4 | Output | 3840x2176, 81 frames, text-to-video. The reference script hardcodes 30 fps (81 frames = 2.7 s) | https://github.com/zhangzjn/T3-Video (infer_multi_gpu.py) |
| F5 | Licence | T3-Video: Apache 2.0, plus "follow the Wan-Video/Wan2.1 LICENSE". Wan2.1-T2V-1.3B card: Apache 2.0. Apache 2.0 permits commercial use; the Wan licence text itself was NOT read (only the HF card) | https://huggingface.co/APRIL-AIGC/T3-Video , https://huggingface.co/Wan-AI/Wan2.1-T2V-1.3B |
| F6 | Official inference stack | DiffSynth-Studio fork (`diffsynth/pipelines/wan_video_new`), PyTorch bfloat16, `infer_multi_gpu.py` (one process per GPU via `CUDA_VISIBLE_DEVICES`, `--num_groups` default 8), CPU offload + `enable_vram_management()`, `--tiled` VAE flag default false, 50 steps default | https://github.com/zhangzjn/T3-Video |
| F7 | Window attention implementation | In `diffsynth/models/wan_video_dit.py` class `SelfAttention`: pure `einops.rearrange` reshapes into "remote" (n_t,n_h,n_w) and "close" (w_t,w_h,w_w) windows, then standard attention via `flash_attention()` (FA3 / FA2 / SageAttention / fallback `F.scaled_dot_product_attention`); two passes averaged. Window layout cycles every 5 layers; 1.3B config uses (n_t,n_h,n_w) = (21,1,1) and (1,17,30), i.e. 8x8-token windows on the 4K token grid. No CUDA-only ops | https://github.com/zhangzjn/T3-Video/blob/main/diffsynth/models/wan_video_dit.py (summary by fetch tool, key lines not independently re-read) |
| F8 | Paper speed claim | Wan2.1-1.3B native 4K DiT-only latency 39,661.7 s (50 steps) baseline vs ~1,857.4 s with T3 + deployment tricks (21.4x). GPU model NOT stated in the text we could read; FlashAttention2 | https://arxiv.org/html/2512.13492v1 |
| F9 | Paper memory claim | "less than 60 GB" at 4K x 81 frames inference; training 179.4 GB | same |
| F10 | Quality claim | +4.29 VQA, +0.08 VTC on 4K-VBench (their own benchmark; standard VBench cannot take 4K inputs) | same |
| F11 | Authors' limitations | Limited by data (42K clips) and batch 64; theoretical MAC cut 43.0x but real 21.4x | same |
| F12 | Base Wan2.1-1.3B | 8.19 GB VRAM for 480p, ~4 min for 5 s 480p on RTX 4090; 480p primary, 720p "less stable" | https://huggingface.co/Wan-AI/Wan2.1-T2V-1.3B |
| F13 | HF Space | `huggingface-apps/t3-video-4k` returns HTTP 401 to unauthenticated fetch (private/org-gated or hidden). Owner's screenshot (15 steps, one pass, no upscaler) is the only source for the 15-step setting; not independently verified | fetch 2026-10-11 |
| F14 | Other runtimes | No GGUF, no ComfyUI node, no diffusers pipeline for T3 in the README (the fetch summary of the model card mentions `DiffusionPipeline.from_pretrained("APRIL-AIGC/T3-Video")`, but the repo has no `model_index.json`; treat as unverified/likely boilerplate). Official path = DiffSynth fork only | https://huggingface.co/APRIL-AIGC/T3-Video |

### Our stack facts (local, checked 2026-10-11)

- Hardware: Ryzen AI Max+ 395, Radeon 8060S (gfx1151), 128 GB unified, Windows 11.
- PyTorch with ROCm exists: `C:\Users\asd\Bossman\media-runtime\comfyui-venv` = torch 2.9.1+rocm7.2.1, einops, transformers, safetensors, psutil, PIL. Missing there: diffsynth, imageio, cv2, accelerate, ftfy, modelscope, flash_attn (not available on Windows ROCm; SDPA fallback is in F7).
- Known ROCm-Windows pitfalls (memory note 2026-10-10): int8 kernels crash, MIOpen VAE-encode fails (set `MIOPEN_FIND_MODE=FAST`), Triton cannot compile, `video_profile` 1 OOMs on unified memory. VAE decode of 4K is a MIOpen conv3d workload: highest risk.
- sd-cli Vulkan (`media-runtime\sdcpp\vulkan\sd-cli.exe`) runs Wan2.1 VACE 1.3B (~31 s/frame at 576x480). sd.cpp has NO window-attention path for T3, so it cannot run T3 weights faithfully (full attention at 4K = ~685k tokens, impossible). Porting F7 to ggml is feasible in principle (reshape + attention) but is a C++ change, out of scope for this evaluation.
- Local copies reusable for the control run: `models\media\wan21-vace-1.3b\wan_2.1_vae.safetensors`, `media-runtime\ComfyUI\models\...\umt5_xxl_fp8_e4m3fn_scaled.safetensors` (formats may not match DiffSynth loaders; the official `Wan2.1_VAE.pth` + `models_t5_umt5-xxl-enc-bf16.pth` will likely be downloaded, to be measured and reported).

## 2. Unknowns (to be resolved in Phase 2, recorded not assumed)

- U1: Does the DiffSynth fork run on torch ROCm 7.2.1 Windows gfx1151 at all (bf16 SDPA, conv3d in VAE)?
- U2: Are window counts (n_h, n_w) derived from the input shape or hardcoded for 4K? T3 was trained only at 4K; runs at 480p/720p/1080p are out-of-distribution for the window geometry. If hardcoded, lower rungs need `n = tokens / 8` patch (documented as "patched, not official").
- U3: Peak memory on unified RAM (GPU-visible share), and whether the VAE decode at 4K needs `--tiled`.
- U4: Real seconds per step at 4K on this GPU. Paper-based projection (not a measurement): 1,857 s at 50 steps on an unnamed datacenter GPU = ~557 s at 15 steps; an iGPU 10-20x slower implies roughly 1.5-3 h. Expect the 25-minute cap to hit at or before 4K.
- U5: Exact 15-step Space setup (sampler, shift, CFG, distilled weights?). F8 mentions an "8-step distillation deployment" that is not in the public repo.
- U6: Wan licence text (commercial clause) not read; read `LICENSE.txt` of Wan2.1 before any client use.

## 3. Run protocol

Preconditions: no `ffmpeg`/`sd-cli`/`facefusion`/Wan2GP/ComfyUI generation process running, and `C:\Users\asd\Videos\Bossman-Genjutsu\ads-20261011\STATE.md` shows the promo videos finished. Otherwise poll every 10 min, up to 3 h. (On 2026-10-11 at plan time: ffmpeg.exe running, STATE.md absent -> Phase 2 deferred.)

Weights: `C:\Users\asd\Bossman\models\media\t3-video-4k\` (T3 2.84 GB + base Wan2.1-T2V-1.3B). Record download size and time. Free, public, no token.

Fixed settings for every run (T3 and control): 81 frames, 15 steps, bf16, DiffSynth default CFG and sigma shift, DiffSynth default Wan negative prompt, no upscaler, no interpolation, encode with ffmpeg libx264 CRF 14 at 27 fps (81/27 = 3.0 s). `--tiled` only if U3 forces it (recorded).

Ladder (one rung at a time, stop at first failure):

| Rung | Resolution (WxH) | Notes |
|------|------------------|-------|
| L0 | 832x480 control: plain Wan2.1 T2V 1.3B (non-T3 DiT) | same prompt + seed, same pipeline, reference for sharpness/flicker |
| L1 | 832x480 T3 | out-of-distribution geometry (U2) |
| L2 | 1280x720 T3 | |
| L3 | 1920x1088 T3 (1080p padded to multiple of 16) | |
| L4 | 3840x2176 T3 | only if L3 PASS and measured L3 peak memory x scale (~4x tokens) < 100 GB |

Per rung, run P1, P2, P3 (section 5), one seed each, no reruns, no selection. All outputs reported, including failures. Cap: 25 min wall per run (generation + decode). "Stop climbing" = a rung where any prompt hits OOM/crash/timeout; the rung below is the highest working one. If L1 itself fails, stop and write the exact reason plus remote recipe (no renting).

Measurement tooling (all local, saved next to the outputs as JSON): wall time via monotonic clock split into load / denoise / VAE decode; peak process working set and system RAM via psutil sampled every 2 s; GPU dedicated + shared memory via the Windows `GPU Adapter Memory` perf counters sampled every 2 s; torch `max_memory_allocated` where available; seconds per frame = wall / 81.

## 4. FIXED PASS/FAIL thresholds

Applied per (rung, prompt); a rung PASSES only if all three prompts pass gates G1-G6 except where stated.

| Gate | Metric | PASS | FAIL |
|------|--------|------|------|
| G1 Completion | 81 frames at exact target WxH, exit code 0, wall <= 1500 s | all true | OOM, crash, wrong size, > 1500 s |
| G2 Sharpness | Variance of the Laplacian (OpenCV-equivalent 3x3 kernel, gray 0-255 float) averaged over frames 0, 20, 40, 60, 80, native resolution. Reference = same frames of the L0 control, Lanczos-upscaled to the rung's WxH | >= 1.5x reference AND absolute >= 50 | below either |
| G3 Flicker | Mean absolute per-pixel luminance difference between consecutive frames inside the pre-declared static ROI (section 5), 0-255 scale, 80 diffs | <= 2.0 AND <= 1.5x the L0 control value for the same prompt | above either |
| G4 Prompt adherence | Manual checklist of 5 items per prompt (section 5), binary, rated by looking at the 5 sample frames plus a 3-s playback description; rater is the evaluator model, owner may re-rate | >= 4/5 items | <= 3/5 |
| G5 Artifacts | Visible 128 px window-grid seams (8x8-token windows at 16 px/token), tiling seams, face/hand deformation in P2, temporal popping/morphing, text-like garbage | none of the listed severe artifacts; minor ones listed | any severe artifact |
| G6 Memory | Peak system RAM + GPU shared < 110 GB and no swap growth during the run | true | false |

Rung verdict: PASS if G1-G6 pass on all 3 prompts; PARTIAL if G1 passes on all 3 and G2-G6 pass on >= 2 of 3 prompts; else FAIL.
Control L0 is reported but not gated except G1 (it defines the references).

Motion Studio usability verdict (fixed):
- "Usable for ad B-roll/backgrounds" = rung L3 (1080p) or higher is PASS or PARTIAL AND seconds per frame x 81 <= 1500 s.
- "Beats current stack" = the highest PASS/PARTIAL rung has G2 >= 1.5x control (native detail, not upscale).
- Otherwise "not usable on this machine; remote recipe only".
- Identity/face work is out of scope: T3 is text-to-video only; FaceFusion remains the identity path.

## 5. Prompt set (fixed, single seed each)

Common: locked-off camera (so a static ROI exists), 81 frames.

| ID | Seed | Prompt | Static ROI (x0,y0,x1,y1 as fractions of frame) |
|----|------|--------|-----------------------------------------------|
| P1 landscape | 20261011 | "A wide landscape of a snow-capped mountain valley at sunrise, a turquoise river winding through dark pine forest, light mist drifting over the water, golden light on the peaks, static camera, cinematic, highly detailed." | sky, (0.00,0.00,0.25,0.12) |
| P2 person | 20261012 | "A young woman with long dark hair slowly turns her head from left profile toward the camera and smiles slightly, soft window light, plain grey wall behind her, static camera, shallow depth of field, photorealistic portrait." | wall, (0.00,0.00,0.20,0.20) |
| P3 neon street | 20261013 | "A rainy neon-lit city street at night, pink and blue glowing signs reflected on wet asphalt, a few people with umbrellas walking, a car passes slowly, static camera, cinematic." | upper dark sky/building, (0.00,0.00,0.25,0.10) |

Adherence checklists (binary, fixed):

- P1: (1) snow-capped mountains; (2) turquoise river; (3) dark pine forest; (4) mist and golden sunrise light; (5) scene stays coherent for 3 s (no morphing of terrain).
- P2: (1) exactly one woman; (2) long dark hair; (3) head turns from profile toward camera; (4) slight smile at the end; (5) plain grey wall with soft light.
- P3: (1) night rain scene; (2) pink and blue neon signs; (3) reflections on wet asphalt; (4) people with umbrellas; (5) a car passes.

## 6. Deliverables of Phase 2

Per run: MP4, 5 sample PNG frames (0, 20, 40, 60, 80), metrics JSON (wall split, peaks, G2/G3 numbers), checklist and artifact list. Summary table of all runs with PASS/FAIL per gate, highest working resolution, seconds per frame, download size/time. If the model cannot run: exact failure reason + cheapest remote recipe (a single 80 GB-class GPU for ~1 h per 4K clip at 15 steps by the paper's scaling; not rented here).
