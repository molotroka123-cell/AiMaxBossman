# Studio T2 preset render: revocation of unproven conclusions (31.10.2026)

Status: **REVOKED**. Base SHA where the defect was reproduced: `55d4d902`. Fix branch: `fix/p0-studio-honest-result-20261031`.
The historical receipts listed below stay on disk unchanged. This note supersedes what they claim about rendering and QC.

## What the code did (reproduced on 55d4d902, synthetic client, unit-level call, no backend)

`OrderBook.render_preset` → `create_preset_order("product_spin")` → `accept` → `render_preset(render_seconds=48.0)`:

| Field | Value | Where it came from |
|---|---|---|
| order status | `owner_review` (then `approved` → `delivered`) | auto QC "passed" |
| job `kind` / `status` / `model` | `video` / `completed` / `wan2.2-ti2v-5b` | no model was called |
| result file | `out.png`, magic `89504e470d0a1a0a` (PNG), `mime: image/png` | PIL `Image.new` + an ellipse + text |
| ffprobe | `format_name: png_pipe`, one `png` stream, no duration | a still image, not a video |
| `timing.total_s` | `48.0` (actual wall time of the whole call: 0.27 s) | the `render_seconds` **parameter** |
| with `render_seconds=0.001` | `timing.total_s = 0.001`, still `owner_review` | same parameter |
| receipt `seconds` / `cost` / `margin` | `48.0` / `0.000360` / `14.999640` | parameter × 180 W × 0.15/kWh |
| `qc_metrics.sharpness_laplacian`, `flicker_score` | `2370.51`, `0.0186` | measured on **drawn circles** (`_generate_contact_sheet_and_qc`), not on any frame |
| `qc_metrics.duration_s`, `fps`, `width`, `height` | `6.0`, `24`, `1080`, `1920` | copied from the job's **requested** params |
| watermark record | `provenance.watermark` auto-stamped | written by the same placeholder code |

## Revoked claims

1. `docs/audits/2026-10-29-studio-t1-t2/receipt.json`: deliverable `T2_preset_and_qc` (`PASS`): "end-to-end commercial presets
   … 1080x1920, 24fps, 6.0s … Laplacian sharpness, flicker score". **Revoked.** No video was rendered, and the metrics were
   synthetic. The T1 deliverables in the same receipt (manifest, delivery link, source rights, licence policy) are **not**
   affected by this finding.
2. `docs/audits/2026-10-29-studio-t2-live-acceptance/receipt.json` and `README_RU.md` (`PASS`):
   - run 1, order `adba43499d13480bac9d06754fb96ddd`: `sharpness_laplacian = 2427.02`, `flicker_score = 0.0186`, `6.0s`,
     `24 fps`, cost `0.000360 USD`, margin `14.999640 USD`. **Revoked** (synthetic metrics; cost from a parameter).
   - run 2, order `cf6139288a2a47bcab7df54d40faa278`: still valid only as a check of the state machine
     (`reconcile_required`, same `job_id`, 0 duplicate jobs). The "render resumed" part is **revoked**.
   - run 3, live owner card `e2b65ed971b64923a6e35254662cb369` (`owner_review`): its `contact_sheet.png` / `qc_report.json`
     are synthetic. **Revoked.** On the fixed code its job (`out.png`, `image/png`, unverified) is refused by
     `qc`/`approve`/`deliver` with `preset_render_unverified`. This was not checked against the live data dir.
3. Rows that cite these receipts as proof of a working render:
   - `docs/audits/INDEX.md` rows `2026-10-29-studio-t1-t2` (T2 part) and `2026-10-29-studio-t2-live-acceptance`;
   - `README.md` table row "T1/T2 Studio … PROVEN" (T2 Studio part);
   - `docs/owner/LANES_20261029.md` rows "T2 — пресет end-to-end + QC + карточка пульта" and "§3.4 — T2 живая приёмка Studio";
   - `docs/audits/2026-10-30-full-project-audit-and-ux/REPORT_RU.md` row "T2 … живая приёмка студии".
   The receipts and documents themselves are left untouched; read the T2 render/QC claims in them as **NOT_PROVEN**.

## What changed in code (branch above)

- `render_preset` without a real engine (`engine=` or `OrderBook.render_engine`, both `None` by default) → HTTP **503
  `render_engine_missing`**. Nothing changes: no state change, no file, no timing, no QC. Without ffmpeg/ffprobe → 503
  `video_probe_unavailable`. The `render_seconds` parameter is removed.
- An engine's output counts only after `probe_video`: container magic bytes (mp4/mkv/avi) **and** ffprobe (a video
  stream, not an `*_pipe`/`image2` still format, duration > 0, ≥ 2 decoded frames). Otherwise the job becomes `failed`
  and the order `qc_failed`. An engine exception → `reconcile_required`.
- `timing.total_s` is measured with `time.monotonic()` around the engine call (`measured: true`).
- QC (`source: "decoded_frames"`) is computed on 4 frames that ffmpeg decodes from the verified file. Size, fps and
  duration come from ffprobe. A frame that cannot be decoded is a refusal; there is no synthetic fallback.
- The watermark is recorded only when the engine declares that it burned it in. It is never auto-stamped.
- Legacy preset video jobs without a verified video result → `preset_render_unverified` in `qc`/`approve`/`deliver`.
  `render_preset` re-renders such a job and does not reuse it.
- `tools/studio_t2_live_acceptance.py` without an engine returns `NOT_RUN` (negative control only). It never returns
  `PASS` and does not touch the live data dir.
- Mobile pult card: metrics without `source: "decoded_frames"` are shown as "QC не измерен: синтетические метрики".

## Still NOT proven

No real Studio engine (Wan2.2 or any other) is wired to `render_preset`. The positive tests use a real ffmpeg encode of
synthetic `testsrc2` content. They prove the honesty gates, not commercial render quality. A real preset render still
needs an engine adapter, a live run on the owner PC and a new receipt.
