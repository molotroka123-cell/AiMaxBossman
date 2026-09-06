# Video Studio — closure findings (Epoch 3)

The Video Studio **is not on this branch**. Only `AUDIT_VIDEO_STUDIO.md` was merged
here; the code it audits lives on `origin/codex/video-studio`. The audit was written
against `dfce725`, the branch tip is `ac2cde8`, and the work below was done against
the tip.

The fixes therefore cannot land here as code. They exist as a patch that applies to
that branch and takes it from *162 passed / 1 failed* to **187 passed, 11 skipped,
0 failed**. The patch is **not stored in this repository**: it carries a decoy API
key inside a test fixture that proves the redactor strips it, and the repository's
secret-pattern gate correctly rejects that pattern. The gate is not weakened for an
artifact belonging to another branch; the patch was handed to the owner directly.
Whoever lands the merge should ask the owner for it.

## The FFmpeg blind spot, measured

On that branch, with and without real `ffmpeg`/`ffprobe` on PATH:

| | passed | failed | skipped |
|---|---|---|---|
| without FFmpeg | 102 | 12 | 60 |
| with FFmpeg | 162 | 1 | 11 |

**61 tests never execute in CI**, and 12 of them do not skip — they fail outright
with `ValueError: ffmpeg is unavailable`, because `test_video_studio_advanced.py`,
`test_video_studio_retrieval.py` and `test_video_studio_shotcut.py` carry no guard.

Skip guards were deliberately **not** added to those 12. That would convert a
measurable loss of coverage into a silent one. Installing the binary in CI is the
fix; the guard is the cover-up. This is the one item left open, and it is open
because `.github/workflows/` belongs to the release lane, not to that branch.

## Defects closed by the patch

**Export dead end.** The render gate re-hashed the whole output *and* re-ran
independent verification — a full decode, three frame samples and a second full
hash — inside a single fixed 60-second hook budget shared by every hook. Exceeding
it raised a critical hook failure, so an export that was already verified and
already on disk parked in `waiting_approval` with a null download URL and a
permanent 409. Independent verification moved into the executor, where it has the
job's lifetime, and is recorded as a durable receipt; the gate now proves
containment, receipt validity and strong file identity with **zero** subprocesses
and zero whole-file hashes. Verification is not bypassed — it happens once, in the
right place, and its result is durable.

**Export could not be recovered.** A parked run re-executed into its own immutable
artifact directory and failed permanently on a path collision, so approval could not
rescue it. Recovery now republishes from the receipt without re-rendering, and
otherwise renders into a fresh attempt directory.

**Failures collapsed to an exception class name.** The owner saw
`executor failed: ValueError` where the real reason was a duration and stream-start
mismatch. There is now a closed set of typed reason codes — timeout, verifier
failed, media decode failed, missing FFmpeg, resource exhausted, approval required,
policy denied, invalid project state, unknown — with a redactor that strips absolute
paths, secret-shaped strings and long hex, and caps length.

**A GET could amplify into unbounded work.** Every thumbnail, proxy, waveform and
media-file request hashed whole files, up to multi-gigabyte, on the thread pool the
whole Command Center shares, with no deduplication and no cap. Hashing now has its
own bounded pool, identical concurrent requests share one computation, and a
cancelled caller no longer aborts the shared work. Twenty identical requests went
from twenty hashes to one.

**Cached hashes are bound to file identity**, not trusted blindly: device, inode,
size and modification time in nanoseconds. Any difference forces a re-hash. The
honest limit — a rewrite on the same inode, at the same size, with a deliberately
restored timestamp, is not detected without re-hashing — is stated in the code and
pinned by a test so nobody later mistakes identity for content.

**Duration had three disagreeing definitions.** For freeze, title and adjustment
clips the model said one second, the renderer said zero, and the interface produced
`NaN` and drew a clip of undefined width. There is now one canonical definition with
no default: a declared-length clip without an explicit positive duration is an
error, the API answers 422 instead of accepting it, and the interface never renders
`NaN`.

**A real colour defect, exposed only by installing FFmpeg.** Output was *tagged*
bt709 but never *converted* to it, so whether the pixels were converted depended on
whether scaling happened to run: pure blue decoded as one colour at one resolution
and a different colour at another. The conversion is now declared explicitly. The
test that caught it was strengthened rather than loosened — its previous tolerance
could not have held even for correct output.

**One audited defect was already fixed upstream** between the audited commit and the
tip: the thumbnail endpoint no longer triggers a full re-encode. A guard was added
so it cannot come back.

## Not closed

The P2 list from the audit is untouched: nothing is ever deleted, an 8 GiB double
write, unbounded deferred-task retries, a capabilities endpoint that blocks the
event loop on a subprocess, a progress field whose producer and consumer disagree
on its type, `waiting_approval` not treated as terminal by the poller, a
lease-schema mismatch, a 500 where a 422 belongs, and two P2 security gaps — a
content type taken from an uploader-supplied name, and a missing nosniff header.
