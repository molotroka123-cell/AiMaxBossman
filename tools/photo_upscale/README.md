# photo_upscale — local, free, honest photo upscaling (Bossman)

Status: **new, uncommitted** (2026-10-12). Nothing here is wired into the Bossman backend yet; it is a self-contained
tool plus a measured model choice. Run with `cwd = tools`, like `exchange_volume`.

```
python -m photo_upscale.upscale --input X.jpg --out Y.png [--longest-edge 8192] [--model auto] [--passes 1|2|3] [--faces]
                                [--detail 0..1] [--no-backprojection] [--dry-run] [--download-models] [--require-neural]
python -m photo_upscale.measure compare --gt GT.png --test OUT.png [--baseline LANCZOS.png]
python -m photo_upscale.measure synth --out DIR            # the procedural ground-truth corpus
python -m pytest tests/photo_upscale -q
```

## Rule: upscaling does not create detail

Everything beyond the source's native resolution is **reconstructed** (inferred by a network or interpolated). This
tool never labels the exported size as native detail: the JSON sidecar always carries the sentence
`honesty.statement_en` / `statement_ru` and `resolution.native_px` vs `resolution.exported_px`.
It is not evidence of appearance (text, faces, plates). Owner rule: measure before claiming.

## Where FaceFusion is found

No user-specific path is compiled in. `upscale.facefusion_dir()` takes, in this order:

1. `PHOTO_UPSCALE_FACEFUSION_DIR` (this tool only), then `BOSSMAN_FACEFUSION_DIR` (the whole Bossman install);
2. the first ancestor of the repository checkout that has `apps-local/facefusion` (the checkout's parent, or the
   Bossman root when the checkout is a nested worktree);
3. `~/Bossman/apps-local/facefusion` (`Path.home()`).

On the owner's PC all three lead to `<Bossman root>\apps-local\facefusion`, so nothing needs to be set there.
`tests/photo_upscale` covers the order and fails if a drive-letter `Users\<name>` path reappears in the sources.

## What it does

* Neural enhancement with FaceFusion's ONNX models (same files in `apps-local/facefusion/.assets/models`, executed by
  onnxruntime-directml from FaceFusion's `.venv` on the Radeon; no CUDA). If the current interpreter has no
  onnxruntime the CLI re-runs itself inside the FaceFusion venv.
* Overlapping tiles + feathered (sin^2) blend with reflect padding: no hard seams. `seam_excess` in the sidecar is the
  measured gradient excess at the seams over control lines (0 = none).
* Each network application is followed by a decode / back-projection check (area-downscale the result and compare to
  its input, >= 24 dB). A failed step falls back to Lanczos for that step and says so.
* **Data-consistency projection** (iterative back-projection, 2 iterations): the output reduced to source size equals
  the source again. Measured to fix the colour/tone drift of the GAN models without removing the added fine structure.
* `--detail 0..1` pulls the result towards the (projected) Lanczos result: 0 = Lanczos, 1 = full model output.
* Refuses (exit 2, message printed before anything runs) when the requested factor exceeds the declared safe limit,
  when the target is not larger than the source, when the output exists (without `--overwrite`) or equals the source.
* Never overwrites the source. Output is a lossless PNG (+alpha, +ICC kept) and `<out>.json`.
* `--faces`: FaceFusion `face_enhancer` (CLI, DirectML) after the frame enhancer; generative, labelled as such.
* No model available -> plain Lanczos, console + sidecar say `fallback: no neural model` (`--require-neural` -> exit 3).

## Measured results (2026-10-12, this PC: Radeon 8060S, onnxruntime-directml 1.24.4, no CUDA)

Protocol: 12 ground-truth images at 2048 px long edge (6 procedural: text, fabric weave, hair strands, foliage,
architecture+signage, resolution chart; 6 CC0 photos from Wikimedia Commons, licence URL + sha256 in the lab
`provenance.json`: castle/trees, cat fur, shop signage, knit fabric, mountain landscape, portrait). GT -> exact area
downscale x4 -> candidate x4 -> compare with GT. Metrics: `tools/photo_upscale/measure.py` (PSNR, SSIM, GMSD, HF energy
and Laplacian ratios vs GT, HF correlation, HF NMSE). Runtime = seconds per output megapixel measured while other work
shared the CPU (clean re-timing of the shortlist: NOT_RUN; second runs were up to 45 % faster). All 19 FaceFusion frame
enhancers loaded on DirectML and finished all 12 images: **no DML op errors, no crashes, no Smart App Control block**
(return code 0 for each).

x4, clean area-downscaled LR, mean over 12 images (`+bp` = data-consistency projection, `d` = detail strength):

| candidate | PSNR dB | SSIM | GMSD (lower better) | HF energy / GT | HF corr | s/MP | peak RAM MB | licence (code + weights) |
|---|---|---|---|---|---|---|---|---|
| Lanczos | 25.53 | 0.748 | 0.130 | 0.12 | 0.428 | 0.01 | - | n/a |
| bicubic | 25.44 | 0.747 | 0.133 | 0.11 | 0.434 | 0.01 | - | n/a |
| **swin2_sr_x4 +bp d0.6** | **26.40** | **0.803** | **0.101** | 0.40 | 0.538 | ~4.3 | 1854 | Apache-2.0 |
| swin2_sr_x4 raw | 25.42 | 0.795 | 0.110 | 0.84 | 0.518 | 4.1 | 1854 | Apache-2.0 |
| face_dat_x4 +bp (d1.0) | 25.99 | 0.795 | 0.100 | 0.37 | 0.500 | 7.7 | 2847 | CC-BY-4.0 (attribution) |
| ultra_sharp_2_x4 +bp d0.5 | 26.51 | 0.807 | 0.099 | 0.37 | 0.555 | 7.3 | 2714 | Non-Commercial |
| real_web_photo_x4 +bp d0.5 | 26.03 | 0.785 | 0.107 | 0.28 | 0.492 | 13.2 | 1231 | CC-BY-4.0 |
| real_hatgan_x4 +bp d0.5 | 25.97 | 0.783 | 0.111 | 0.28 | 0.484 | 8.2 | 6177 | Apache-2.0 |
| siax_x4 +bp d0.5 | 25.88 | 0.782 | 0.103 | 0.44 | 0.477 | 1.35 | 1215 | WTFPL |
| remacri_x4 +bp d0.5 | 26.02 | 0.791 | 0.103 | 0.42 | 0.497 | 1.2 | 1086 | Non-Commercial |
| nomos8k_sc_x4 +bp d0.5 | 25.77 | 0.774 | 0.113 | 0.30 | 0.457 | 1.35 | 1277 | CC-BY-4.0 |
| real_esrgan_x4 +bp d0.5 | 25.57 | 0.770 | 0.116 | 0.33 | 0.433 | 1.24 | 1210 | BSD-3-Clause |
| real_esrgan_x4 raw | 22.68 | 0.709 | 0.161 | 0.83 | 0.331 | 1.13 | 1210 | BSD-3-Clause |
| siax_x4 raw | 23.82 | 0.736 | 0.107 | 1.26 | 0.428 | 1.23 | 1215 | WTFPL |
| remacri_x4 raw | 23.95 | 0.761 | 0.109 | 1.17 | 0.456 | 1.09 | 1086 | Non-Commercial |
| real_esrgan_x2 twice (x2 -> x2) raw | 21.90 | 0.677 | 0.178 | 0.92 | 0.258 | 4.8 | 737 | BSD-3-Clause |
| real_esrgan_x8 (+ area/2) raw | 22.09 | 0.690 | 0.149 | 0.92 | 0.350 | 5.1 | 3932 | BSD-3-Clause |

Rows not shown (all measured, all in the lab JSON): clear_reality_x4, span_kendata_x4, ultra_sharp_x4,
realistic_rescaler_x4, tghq_face_x8 (GPL-3.0), fp16 variants (identical scores, no reliable speed gain).

What the numbers say, honestly:

* On clean area-downscaled input **no model beats plain Lanczos on PSNR** in its raw form (Real-ESRGAN loses 2.9 dB:
  colour/tone drift plus re-drawn texture; its GMSD is worse than bicubic). Swin2SR is the only raw output that is
  not worse than Lanczos in PSNR (25.42 vs 25.53) and clearly better in SSIM (0.795 vs 0.748).
* The data-consistency projection (+bp) recovers 0.2-1.7 dB for every model; a detail strength of 0.5-0.6 gives the
  best fidelity. Best licence-clean result: **swin2_sr_x4 + projection at detail 0.6** = +0.87 dB PSNR, +0.055 SSIM,
  GMSD 0.130 -> 0.101 over Lanczos. That is a modest, real gain; it is not "new detail".
* Models that look sharpest (siax, remacri, span, realistic_rescaler: Laplacian variance above the GT's) are the ones
  that invent structure: HF NMSE > 1.2 means more misplaced than recovered detail.
* JPEG-degraded LR (area x4 then JPEG q70, same 12 GT; `results_x4_jpeg70.json`): everything collapses to Lanczos level.
  Best: real_hatgan +bp d0.6 24.36 dB, face_dat +bp d0.6 24.30, swin2 +bp d0.6 24.26 vs Lanczos 24.26. Raw GAN
  outputs are far worse (real_esrgan 21.95, siax 22.42) and visibly paint structures that are not in the scene.
* x2 (LR = GT/2, `results_x2.json`, raw): swin2_sr_x4 + area/2 29.79 dB / SSIM 0.921 vs Lanczos 29.28 / 0.892;
  real_esrgan_x2 25.66 and real_esrgan_x4 25.73 are 3.5+ dB below Lanczos.
* x8 (LR = GT/8, 6 images, `results_x8.json`, raw): swin2 20.47 dB vs Lanczos 20.26 (HF NMSE 1.08 vs 0.98); GAN
  models 18.0-18.3 dB. 8x is therefore **unproven -> refused** (`SAFE_MAX_FACTOR = 4`).

### Artifacts seen in 100 % crops (lab `crops/`)

* Small text (about 3 px x-height in LR): GAN models (real_esrgan, siax, remacri, nomos) turn "CONFECTIONERS" into
  plausible but wrong letterforms; Lanczos stays soft but not wrong; larger text (>= 20 px in GT) survives all models.
* Fabric / knit: real_esrgan paints hard dark outlines (oil-paint look); nomos8k adds crack-like lines; siax adds grain
  closer to the truth but not at the right places; face_dat/swin2 stay soft and faithful.
* JPEG input: GAN models invent geometry (shop front turns into unrelated shapes); face_dat injects hair-like strands
  into non-face content; swin2 keeps JPEG blocking (it does not remove it).
* Halos/ringing: not seen at the default settings; plastic skin appears with face enhancers at blend 100.

### Faces (n = 1 portrait fully measured; 2 further CC0 portraits were started and stopped: NOT_RUN)

On cc0_portrait (face crop vs GT, base = swin2 + bp d0.5): base PSNR 34.03 / SSIM 0.895 / ArcFace identity 0.978.
Every face enhancer **lowered** fidelity: gfpgan_1.4 b60 30.69 dB / id 0.949, gfpgan_1.3 b60 29.00 / 0.901,
codeformer b60 29.64 / 0.908, restoreformer++ b60 22.64 / 0.951, gpen_bfr_512 b60 31.34 / 0.965 (non-commercial);
blend 100 is worse than 60 on every metric (gfpgan_1.4 b100 27.77 dB / id 0.889). Conclusion: no face enhancer
qualified as a *faithful* improvement; `--faces` is opt-in, provisional default gfpgan_1.4 at blend 60 (Apache-2.0
code + weights verified at github.com/TencentARC/GFPGAN; trained on FFHQ, whose images are CC BY-NC-SA: legal review of
training-data terms NOT done). ArcFace is used for measurement only (non-commercial licence, not part of the product).

### Recommended defaults

| mode | model | licence | notes |
|---|---|---|---|
| faithful (default `--model auto`) | `swin2_sr_x4` + projection + detail 0.6 | Apache-2.0 (code verified at github.com/mv-lab/swin2sr; weights carry no separate licence statement; trained on DIV2K/Flickr2K, dataset terms not reviewed) | factor <= 4, ~4 s per output MP, 1.9 GB RAM |
| faces (`--faces`) | `gfpgan_1.4`, blend 60, on top of the frame enhancer | Apache-2.0 | generative; fidelity measured on one portrait only |
| more visible texture, licence-clean | `face_dat_x4` +bp d0.8 (CC-BY-4.0, attribution: Helaman) or `siax_x4` +bp d0.5 (WTFPL) | | ~7.7 / 1.35 s per MP |
| never for fidelity | `real_esrgan_x4` raw, `real_esrgan_x2` chain, `*_x8`, `realistic_rescaler_x4` | | |

Non-commercial weights (ultra_sharp*, remacri, clear_reality, span_kendata, codeformer, gpen_*) are only used with an
explicit `--model NAME`; `--model auto` ignores them unless `--allow-noncommercial`. This is not legal advice.

### Seam notes

Feathered tiling is exact for local models (unit test: tiled == untiled within 6 levels). For Swin2SR the tiled and
whole-image outputs differ (mean 1.2 levels, max 165 on single high-contrast pixels: window attention sees different
context), and `seam_excess` of a real run with only 3 seam lines is content-biased (tiled 0.31 vs untiled control 0.24
at the same positions). Treat `seam_excess` as meaningful only with many seams or relative to the control.

### Contextual generative refinement (tile img2img at <= 0.25 strength): feasibility only, NOT BUILT, NOT RUN

* `<Bossman root>\media-runtime\sdcpp\vulkan\sd-cli.exe` supports `-M img_gen -i init.png --strength 0.2`,
  `--mask`, `--vae-tiling`, `-M adetailer`, `--hires`, `-M upscale --upscale-model <esrgan .pth>`; SDXL base and
  epicRealism XL checkpoints are on disk. Feasible locally, no cloud.
* Not wired: `bcc/direct_gen/sdcli.py` + `bcc/studio/providers/sdcpp.py::_argv` pass `-i` only for the Wan video model
  and `-r` for Qwen-Edit; there is no image img2img argv, no tile loop, no feathered multiband blend, no registration
  check.
* Cost (unmeasured estimate): an SDXL 1024 tile takes tens of seconds on Vulkan -> an 8192 px image is about an hour.
  The one-tile probe (`refine_probe.py` in the lab) was queued but stopped at the wrap-up: **no measurement exists**
  that refinement helps or hurts fidelity. Do not claim it.

## NOT_RUN / not proven

* Clean timing of the shortlist in isolation; face benchmark on portraits 2-3 and with a face_dat base; x8 for
  face_dat/nomos; the sd-cli refine probe; real-world (not synthetically degraded) inputs; video frames.
* Large-image (>= 8192 px) end-to-end run: only 2048 px outputs were produced; memory/time at 8192 px is extrapolated.
* Legal review of the weights' training-data terms.

## Files

`tools/photo_upscale/{__init__,upscale,measure}.py`, `tests/photo_upscale/test_photo_upscale.py` (31 tests),
`.agents/skills/photo-upscale-local/SKILL.md`. Lab (outside git): `<Bossman root>\apps-local\photo-upscale-lab\`
(corpus, outputs, JSON results, crops, scripts); demo run:
`<Bossman root>\creative-runs\PHOTO-UPSCALE-20261012\demo\`.

