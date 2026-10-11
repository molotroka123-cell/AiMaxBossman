> Ported from branch feat/hair-mask-20261010 @ c82c71d7 (10.10 15:34). The same path carries a NEWER, different design in HAIR_MASK_V2_20261010.md (feat/ads-queue-20261010 @ 0bdd4af0, 11.10 02:24) - that one wins as the current spec; this file is kept as the alternative, stricter-on-measurement metric set M1..M6 (independent matte, flicker vs v1 baseline). Both are fixed-before-measurement threshold sheets; neither has a result recorded in git.

# Genjutsu HAIR COLOUR: mask v2 (10.10.2026)

Owner feedback 10.10: «цвет волос очень удачный и оч криво вырезан» (the colour is good, the cut-out is crooked).
This file is written and committed BEFORE any v1/v2 measurement is run (thresholds are not tuned to the results).
Results are appended below the line `## RESULTS` in a later commit.

## 0. What produced the owner's clips (read from code, not guessed)

- `tools/trend_edit/build_trends.py trend-b` on `ref-promo-08.mp4` (1488x1128, 24 fps, 353 frames), 9:16 crop = 720x1280.
- Per frame: `ctx.hair()` -> `edit_core.recolor()` = flat Lab shift (`L += (tL - meanL) * lightness%`, `ab += (target - ab) * strength%`)
  blended with the SegFormer-B2 `hair` soft mask from `parsing_worker.py` (guided filter radius ~7 px, smoothstep 0.2..0.8,
  EMA 0.6 over time, no optical flow, no skin/matte constraint).
- Presets (`edit_core.HAIR`): platinum `#ebe6da` strength 70 / lightness 55 -> 30% of the ORIGINAL chroma stays and the L shift is only 55%.

## 1. Test set (fixed)

| id | source | frames |
|---|---|---|
| S01..S14 | `video-testset-private/ref-promo-08.mp4` (the source of BOTH owner clips; the 9:16 clip is a crop of the same frames) | 0-based idx `12 + 25*k`, k = 0..13 (14 frames) |
| H01 | ref-clip-01.mp4, t = 3.0 s | sunglasses, concert background |
| H02 | ref-clip-02.mov, t = 1.0 s | wind-blown loose hair |
| H03 | ref-clip-03-pult.mp4, t = 9.0 s | back/side view, close-up |
| H04 | ref-clip-04.mp4, t = 1.0 s | bun + hand over the face |
| H05 | ref-clip-04.mp4, t = 7.0 s | hand on the hair |
| H06 | ref-clip-05.mp4, t = 1.0 s | bun + hand at the hair |

Temporal sequences (for the flicker metric): promo-08 frames 130..153 and 260..283; ref-clip-04 frames 0..23 (24 frames each).

## 2. Ground truth-ish alpha

Robust Video Matting (RVM MobileNetV3, `models/media/rvm/rvm_mobilenetv3_fp32.onnx`, GPL-3.0; used ONLY as an offline
measuring instrument, never shipped), run recurrently over >= 8 preceding frames of the same clip, downsample ratio 0.5.
SegFormer's own mask is NOT ground truth. v2 production matting uses a DIFFERENT model (MODNet, Apache-2.0), so the
measurement is not circular.
Limitation (stated up front): a person matte only knows hair/background; the hair/skin and hair/clothes borders have no
ground truth, so M1/M2 judge the outer outline only; M3 judges skin leakage by colour, independently of any mask.

All distances are in px at 720p: `px720 = px * min(1, 720 / min(W, H))` (1488x1128 -> x0.638; 720x1280 and 400x400 -> x1).

## 3. Metrics and PASS thresholds (per recolour output; means over the test set unless "worst" is said)

| id | metric | definition | threshold |
|---|---|---|---|
| M1 | boundary adherence | recolour region R = {alpha_out >= 0.5}; boundary pixels of R that lie within 12 px720 of the matte's 0.5 contour; mean distance to that contour | mean <= 2.0 px720 |
| M2 | leak | pixels of R that are > 6 px720 outside the matte (matte < 0.5), as a share of area(R) | mean <= 1.0 % |
| M3 | skin | share of R's pixels that are skin-coloured IN THE ORIGINAL frame (YCrCb Cr 140..175, Cb 77..125, Y >= 70, HSV S >= 0.12) | mean <= 0.5 % |
| M4 | temporal flicker of the alpha | sum(abs(alpha_t - warp_t(alpha_{t-1}))) / area(R_t), Farneback flow of the RGB frames, mean over the sequences | v2 <= v1 baseline (the shipped v1 masks incl. their EMA) |
| M5 | target colour | Delta-E76 between the alpha-weighted mean Lab of the recoloured hair and the Lab of the named colour's swatch (`edit_core.HAIR`), per colour | mean over frames <= 15 for EVERY colour (blue, green, white, black, copper, platinum) |
| M6 | shading | std of Lab L inside R after / before recolour | mean ratio >= 0.8 for every colour |

Overall verdict per version: PASS only if M1..M6 all pass; otherwise it is reported metric by metric, failures not hidden.

## 4. v2 design (what is being built)

1. alpha: SegFormer hair prob -> trimap (erode / dilate) -> guided-filter refinement (colour guide) -> bounded by a MODNet
   person matte on the hair/background boundary, strands taken from the matte where the pixel is hair-like.
2. skin / hands / neck / face removed with the parsing `skin` mask (soft) plus an independent YCrCb skin check, and the
   `face` hole; hair seen between fingers is kept only when it is hair-like (dark/similar to the core hair colour).
3. recolour in Lab, L kept (strands, highlights) but re-centred on the named colour with a soft shoulder; light targets
   (white/platinum) lift L with chroma desaturation instead of a flat grey cap; residual source chroma is removed
   (no more orange platinum); root shadow kept by contact darkening near the skin/scalp; light deblock/denoise of the
   recoloured region only.
4. temporal: flow-warped EMA of alpha (as in `tools/genjutsu/temporal_smooth.py`) with a photometric gate at occlusions.
