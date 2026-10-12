---
name: photo-upscale-local
description: Enlarge a photo locally and for free (FaceFusion ONNX models on the Radeon via DirectML) with measured model choice, tiled seam-free processing, a refusal above the proven factor, a lossless PNG plus an honest JSON sidecar, and a verification step (size, decode, seams, sharpness, back-projection). Use when the owner asks to upscale / enlarge / "make it sharper" for a still image.
compatibility: BOSSMAN (Windows, Radeon 8060S, FaceFusion .venv onnxruntime-directml, numpy + Pillow); no CUDA, no cloud
metadata:
  owner: bossman
  version: "1.0"
  learned_from: owner rejected soft Real-ESRGAN video upscales 2026-10-11 ("upscaling does not create detail"); benchmark 2026-10-12 (tools/photo_upscale/README.md)
---

# Photo upscale, local and honest / Локальный апскейл фото без самообмана

## Rule zero / Правило ноль

EN: Upscaling does not create detail. Everything beyond the source's native resolution is **reconstructed**. Never call
the exported size "native", "4K photo" or "original detail". Measure before you claim.

RU: Апскейл не создаёт деталей. Всё, что сверх нативного разрешения источника, — **достроено моделью**. Не называть
экспортированный размер «нативным», «настоящим 4K» или «исходной детализацией». Сначала измерить, потом заявлять.

## When to use / Когда применять

- A still image must be bigger (print, 4K background, larger crop) and the source is the best available.
- The owner explicitly asked for "upscale / enlarge / sharper".

Do NOT use / Не применять:

- as proof of appearance (documents, faces of real people, plates, evidence): added pixels are inferred;
- on video frames without first measuring (video: see `video-face-swap-facefusion` / owner rule "no soft upscales");
- for factors above the declared safe limit (the tool refuses; do not look for a way around it);
- with personal photos on any cloud / foreign pod. Everything here is local; nothing is uploaded, ever.

## Inputs / Входные данные

- `--input` a local image path (never modified).
- `--out` a `.png` path (a `<out>.json` sidecar is written next to it). The tool refuses to overwrite an existing
  output without `--overwrite`, and never overwrites the source.
- Optional: `--longest-edge` (default `min(8192, safe factor x source)`), `--passes 1|2|3`, `--model auto|<name>`,
  `--faces`, `--dry-run`, `--require-neural`.

## Steps / Шаги

1. **Look at the source** (size, content). If it is already >= the target, stop; there is nothing to do.
2. **Dry run** (cwd = `tools`):
   `python -m photo_upscale.upscale --input X --out Y.png --longest-edge N --dry-run`
   Read the plan line (factor, model, steps). If it says `REFUSED` (exit 2), report the refusal and the largest
   allowed edge; do not retry with a bigger number.
3. **Run** the same command without `--dry-run`. If the model weights are missing add `--download-models`
   (weights only; no image is sent). If no neural model is available the tool prints
   `fallback: no neural model` and produces plain Lanczos; say so to the owner, do not present it as AI upscaling.
4. **Verify** from the sidecar (all must hold, otherwise the result is not delivered):
   - `output.width/height` equal the plan, aspect equals the source (+-1 px), `output.decode_check == "ok"`;
   - every `passes[i].check == "ok"` and `consistency_psnr_db >= 24`; no `fallback.used`;
   - `passes[i].seam_excess`: meaningful only with many seams (content bias of +-0.3 with 3 seam lines); judge seams
     by eye on 100% crops at tile borders (`tile`/`overlap` are in the sidecar);
   - `measured.sharpness_ratio_vs_lanczos` is transparency, not a pass/fail gate: faithful settings gave about 10-40x
     (Laplacian variance) because Lanczos is nearly flat at that scale; GAN models at full detail reach 100x+ and
     invent structure. Only a ground truth proves hallucination, so compare 100% crops;
   - `measured.backprojection_psnr_db.output` is high (the output reduced to source size matches the source);
5. **Look at 100% crops** of text, faces, hair/fur, repeated texture. Real artifacts seen in the benchmark: garbled
   letterforms in small text, plastic skin, painted-over fur, halos on edges. If text is present and unreadable in the
   source, the result will contain *plausible-looking wrong letters*: say so.
6. **Hand over** (below), with the honesty sentence from the sidecar.

## Faces / Лица

`--faces` runs a FaceFusion face enhancer (generative restoration prior) after the frame enhancer. It re-draws skin,
eyes and teeth. Blend 60 keeps more of the real face; the sidecar sets `honesty.faces_generative`. Say in the caption
that faces were restored by a model. Never use for identification.

## Hand-off to Bossman / Передача в Bossman

- Folder: `C:\Users\asd\Bossman\creative-runs\<RUN-ID>\photo-upscale\<source-stem>\`
  containing `<stem>__x<factor>.png` and `<stem>__x<factor>.png.json`. Never put the PNG into git; the sidecar is the
  receipt (source sha256, model sha256 + licence, parameters, native vs exported px, measured ratios, timings).
- Caption (owner-facing, EN or RU), always with numbers: "`<native>` -> `<exported>` (xN), model `<name>`
  (`<licence>`), detail beyond `<native>` is reconstructed, not captured. Checks: seams `<value>`, sharpness vs Lanczos
  `<value>`, back-projection `<dB>`."
- Provenance in captions: state where each part was generated (this PC, DirectML). Commercial use: only models with
  `commercial_use_ok: true`; CC-BY models need attribution (named in the sidecar).
- Result goes only to the owner's pult / the requesting job. No cloud, no foreign pod, no paid API.

## Failure handling / Если что-то пошло не так

- DirectML op error or crash: the tool records the exact exception text and falls back to Lanczos for that run
  (`fallback.reason`). Do not retry blindly: try `--device cpu` (slow) or a different `--model`.
- Windows App Control / Smart App Control blocks an unsigned engine: that is a block, not a model error; report it.
- GPU busy with another Bossman job: do not run a second heavy job; wait or use `--device cpu` for small images.

## Verification commands / Проверка

```
python -m photo_upscale.measure compare --gt GT.png --test OUT.png --baseline LANCZOS.png   # when a GT exists
python -m pytest tests/photo_upscale -q                                                      # tool tests
```

Not proven (state it, do not claim): contextual generative refinement (tile img2img at low denoise) is not built;
results on degraded real-world sources differ from the clean benchmark; see `tools/photo_upscale/README.md`.
