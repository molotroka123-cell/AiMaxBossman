# SDR color fidelity checkpoint

Baseline: `7b0ed1f` (CFR frame preservation). This checkpoint fixes an actual
SDR sample/matrix mismatch and corrects an independent color-test sampling flaw.

## Renderer change

FFmpeg overlay defaults to YUV420. The graph implicitly converted RGB into YUV
before output, then tagged samples as BT.709 without ensuring that conversion
used BT.709. Midtones visibly shifted. Both ordinary and adjustment overlays now
compose in RGB. The final scale explicitly converts to limited-range BT.709
YUV420, and encoder matrix/range tags match those samples. The format constraint
is adjacent to scale so intermediate filters cannot negotiate a different format.

## Independent pixel evidence

New tests generate six color bands, including midtones, encode separate BT.709
and SMPTE170M fixtures, then decode native RGB pixels from source and outputs.
Both 360×180 export and 180×90 preview must match source interior pixels within
four levels per channel. Output metadata must identify BT.709 and limited range.
These tests do not downsample their pixel oracle.

Both tests fail when run against the unmodified baseline renderer loaded into
an isolated module (no checkout mutation):

- BT.709: expected `(210, 23, 35)`, observed `(225, 40, 33)`.
- SMPTE170M: expected `(208, 24, 34)`, observed `(223, 41, 31)`.

Candidate: **2 passed in 18.37 seconds** (four real renders).

The older concat/range test combined YUV conversion with `scale=1:1`.
Local swscale produced different sampled RGB for identically colored native
frames of different dimensions. The helper now converts to RGB before shrinking.
Its existing `<5` tolerance is unchanged. This is separate from the renderer
fix: native-pixel tests above fail the old renderer independently of that helper.

## Acceptance and limits

**Focused renderer + CFR suite: 52 passed, 2 skipped in 187.30 seconds.**
Command: `PYTHONPATH=command-center:bossman-core:. /tmp/bossman-epoch4-venv/bin/python -m pytest command-center/tests/test_video_studio_render.py command-center/tests/test_video_studio_cfr_frames.py -q`.
The skips are the optional Windows CV interpreter and downloaded Whisper model.
Secret-pattern scan and `git diff --check` both passed. Includes
real title, nested sequence, adjustment layer, LUT/blend, temporal composition,
preview/range and exact CFR frame checks. No tests are disabled for this fix.

RGB compositing can use more intermediate memory than YUV420; performance and
peak memory are unmeasured. This is an SDR matrix/range fix, not full ICC/gamut
or transfer-function color management. HDR, GPU encoders and Windows are not
certified by these Linux software-encoder tests. Existing optional model/platform
skips remain explicitly unverified.

## Rollback

Revert this checkpoint. No project migration or source-media rewrite is involved.
Previously exported artifacts are unchanged; reverting restores the known color
mismatch and must remove this checkpoint's fidelity claim.
