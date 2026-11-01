# P0 Studio false PASS: `art_v2.vlm_critic` (31.10)

**Found:** by the CI agent while fixing bandit B324. Confirmed by reading the code.

**What it did:**
- Every keyframe was a hard-coded "studio render" metric; the media was never opened.
- A "flaw" was "repaired" by re-reading the same constants without the defect.
- `final_score = max(score, target_pass_score)` raised every score to at least 0.92.
- It then signed a PASS receipt.

**Live callers:** none in `bcc`, `apps` or `tools`. It is exported from `bcc.art_v2`, and any future caller would have received a signed false PASS.

**Fix:**
- Metrics count as measured only when the caller passes real per-keyframe measurements with a non-synthetic `source`.
- Synthetic input gets the status **NOT_MEASURED** and can never pass.
- The score is never raised.
- The critic regenerates nothing, so a correction is only recommended (`applied: False`).
- A flawed or blurry measurement is **REJECTED**.
- `sharpness_pass` is computed; it was previously always `True`.

**Tests:** `command-center/tests/test_art_studio_v2_wunderkind.py` (15 passed):
- `test_vlm_art_critic_synthetic_input_is_never_certified` (negative: no input, simulated flaw, a bare path)
- `test_vlm_art_critic_flawed_measurement_is_rejected_and_score_is_never_raised` (negative: the old code returned PASS 0.92 here)
- `test_vlm_art_critic_real_measurements_pass_with_a_signed_receipt`

**Not proven:** no real ffmpeg/ArcFace measurement is wired into this critic. Real Studio QC remains `direct_gen/orders.py` (ffprobe, measured time), see `studio_revocation.md`.
