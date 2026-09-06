# Frame-aligned linked split — editing checkpoint

Base: `ac2cde8eb3e0f4985178cb3fb54a7419aeb3d8e1` (`codex/video-studio`).
Scope contract: `13db72ee0c8b98cd2c5c5897e04a844fffb2536c`,
`docs/v4/CREATIVE_APPS_CONTRACT.md`. This checkpoint qualifies editing, not export
frame accuracy or professional-editor parity. No runtime integration into the
Epoch 4 branch or change to Fable's V3 work is included.

## Behavior

Existing `clip.split` accepts exactly one coordinate: `at` (legacy ticks) or
`frame` (absolute zero-based sequence frame). Frame conversion uses rational FPS
and half-up integer rounding. The inspector, toolbar and S shortcut now submit
frame coordinates through the existing revision-guarded command API. Existing
native `video.clip.split` accepts the same command payload.

A reciprocal linked A/V pair splits atomically at the same timeline position.
The two right halves receive a new shared group and reciprocal links, so moving
that pair does not drag the left halves. Source files and source references are
not rewritten. Existing canonical history provides dry-run, undo, redo and
idempotent replay. Partner track locks and human edit leases block the whole
operation; stale revisions do not consume an edit.

Invalid coordinates, inconsistent/cross-sequence links, larger ambiguous groups,
nonconstant sources and speed rounding that would create a tick gap fail before
persisting. Explicit one-sided splitting of a linked clip is rejected; unlink
it first. Nonlinear eased curves are deliberately unsupported for frame/linked
split until their full curve shape can be preserved. Linear and hold curves are
supported, including a cut exactly at a hold-to-linear value jump.

Example agent command, inside the existing mutation envelope:

```json
{"type":"clip.split","clip_id":"current-clip-id","frame":37,"with_links":true}
```

## Evidence

Linux/Python 3.12; local deterministic fixtures, real SQLite, authenticated ASGI
API and native tool handler. No paid/model/cloud calls.

- `pytest command-center/tests/test_video_studio_frame_split.py command-center/tests/test_video_studio_domain.py -q`:
  **72 passed / 1 strict xfailed**, 18.55 seconds (33 new passing tests, 39 existing).
- `node --test command-center/ui/tests/video_studio_state.test.mjs`:
  **11 passed**; editor module syntax check passed.
- Real local FFmpeg fixture: immutable 1-second test-pattern source and tone;
  linked split output decodes with audio, contiguous timestamps and the same
  frame count as the unsplit baseline. Source SHA256 remains unchanged.
- Independent review found a hold-key boundary corruption; the exact-key value
  fix and regression are included. Full browser interaction, Windows and GPU
  render qualification were not run for this checkpoint.

## Historical baseline export defect — resolved by the CFR checkpoint

A 1-second CFR source has 25 frames at 25 fps. The existing renderer emits
**24 frames for the unsplit baseline and 24 for the split result**, despite its
current duration/decode verifier reporting PASS. `render.py` and `media.py` are
byte-for-byte unchanged from the base. The unsplit fixture uses unchanged
clip.add/clip.detach_audio operations; no split code participates in that render.

At the initial editing checkpoint, `test_open_baseline_exact_export_frame_count`
retained the exact 25-frame oracle as a **strict xfail**, separately from the
passing edit/non-regression fixture. The following CFR checkpoint fixes the
renderer and replaces that expected failure with normal passing
`test_exact_export_frame_count`. See `CFR_RENDER_CHECKPOINT.md` for endpoint
content, rational-range and independently counted frame evidence. Historical
counts above describe the initial editing checkpoint, not the current renderer.
Full export acceptance remains open because a separate color-preview baseline
failure and platform/hardware qualification are outstanding.

Other existing limitations outside this slice: trim source-in/keyframe timeline
semantics need a dedicated review; nonlinear easing is not losslessly split by
the legacy generic tick path. No blanket claim is made for ripple, transitions,
multicam or project-wide link integrity.

## Rollback

Revert this checkpoint's code while retaining existing project documents,
source media and revision history. No schema or database migration is needed.
Undo restores the complete pre-split document. Older code can read documents
created here, but reverting restores the old linked-split limitation; do not
replay already accepted mutation operation IDs with changed payloads.
