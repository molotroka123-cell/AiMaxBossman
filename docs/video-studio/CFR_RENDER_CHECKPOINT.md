# CFR render endpoint checkpoint

Parent editing commit: `8dbafd69de3dc61bc2f6ca3d966b929ca77c78c8` (published
with identical tree at `5a3053319583e04874b3efd35647af8f4adc08cb`). Original
renderer baseline: `ac2cde8eb3e0f4985178cb3fb54a7419aeb3d8e1`.

Scope: restore real final frames in CFR rendering and independently verify the
promised frame count. No schema, UI, permission, paid API or broad export change.
Stream-copy/VFR semantics are unchanged and not newly certified.

## Cause and fix

A real one-second composition at 25 fps contained 25 frames before the final
`fps` filter, including the distinct source-derived frame at 0.96 seconds.
Framesync EOF timing caused the redundant final filter to emit only 24.
`eof_action=pass` alone did not restore it. A bounded lookahead of one sequence
frame plus one output frame lets `fps` emit the real final frame; an exact
frame-count trim prevents lookahead frames from entering the published output.
Distinct red first/blue last fixtures prove this does not merely duplicate the
penultimate green frame to satisfy a counter.

The renderer passes an immutable expected frame count into `verify_output`.
That verifier uses bounded `ffprobe -count_frames` / `nb_read_frames`, rather
than trusting container `nb_frames`. Missing or mismatched observed count fails
verification before artifact publication. Existing decode, audio, dimensions,
rate, duration and artifact checks remain in place.

## Timing contract

CFR output represents the half-open requested interval. Each exact sequence
frame endpoint is recognized within half a microsecond of its rational time;
recognized range endpoints are trimmed using integer sequence-frame indices.
This preserves a selected first frame whose timestamp rounds upward to ticks.
When both endpoints match output frame boundaries, their frame indices are
subtracted directly; otherwise count is ceil((duration_ticks - 0.5) × fps /
1,000,000), at least one frame for a nonempty interval. This avoids interpreting
rounded rational boundaries as accidental extra frames. Arbitrary intervals
include their final partial frame. Two independently rounded range endpoints
can differ by nearly one tick, so their identities are checked separately.

Verified examples include 25, 30 and 30000/1001 fps; one-frame rational export;
short and one-second exports; rational ranges 1→3, 1→7, 2→4 and 3→7 with a red
marker specifically on the selected first frame and blue on the selected last.
Verification sampling chooses actual frame positions, including one-frame
exports, instead of seeking beyond their sole frame using container duration.

## Evidence and limits

**Final focused suite: 53 passed, zero skips/xfails, in 71.24 seconds** on Linux/Python 3.12 with local FFmpeg. Tests:

```text
command-center/tests/test_video_studio_cfr_frames.py
command-center/tests/test_video_studio_frame_split.py
```

`test_exact_export_frame_count` is now a normal passing 25-frame oracle; its
strict xfail has been removed. Real fixtures check decoded count, contiguous
PTS, first/last content, audio presence and unchanged source SHA256. Wrong
expected count is rejected by independent verification.

Broader render+split regression during development: **66 passed / 2 skipped /
1 failed** in 154.51 seconds. The failure is
`test_pixel_evidence_concat_range_preview_equivalent`: a 320×180 full export
and 160×90 range differ in the existing one-pixel color comparison. The same
test fails on pristine detached `ac2cde8` (8.73 seconds), as well as on the
candidate (9.99 seconds); its oracle is unchanged. Full renderer acceptance
therefore remains OPEN. The two skips are the unavailable optional Windows CV interpreter and downloaded Whisper model; they are not passes.

Decoded frame counting adds a verification pass and increases render completion
latency; large-video cost has not been benchmarked. Linux local FFmpeg evidence
does not certify Windows, GPU encoders, every container/codec or full professional
editor parity. Existing color-management work remains a separate checkpoint.

## Rollback

Revert this code checkpoint, retaining sources, accepted project revisions and
already verified output artifacts. No data migration is required. Reverting
restores the known final-frame-loss behavior and must not retain the claim that
CFR output satisfies the exact frame-count oracle.
