# Bounded media-read verification checkpoint

This change keeps full content hashing on authenticated original-media reads,
while coalescing only simultaneous requests with identical owned path, expected
SHA-256, and file identity. A process-wide dedicated executor uses two workers
and admits at most sixteen distinct pending identities, avoiding unbounded
hash work and contention with the general asyncio thread pool.

Completed checks are never reused: every sequential read hashes again. Stat
identity is only a concurrent-replacement guard, not a content checksum. The
reference is copied before queueing, containment is checked for each caller,
and file identity is checked before and after verification and before return.
Cancellation of one caller cannot cancel queued/shared checks. Completed jobs
release admission even when all callers disconnect; exceptions are observed.
Overload fails closed as HTTP 503 with Retry-After: 1.

## Red-team cases

- Twenty overlapping service reads share exactly one full real-media hash.
- Same-size mutation with restored mtime is rejected on the next read.
- Mutation during hashing is rejected, even if the hash itself was valid.
- Mutable reference cannot redirect an already admitted read.
- Cancellation cannot free admission while work remains queued.
- A completed future awaiting cleanup cannot be reused as cached evidence.
- Invalid paths/digests are rejected independently for each caller.
- Transient hash failure is not cached; later valid verification can recover.
- Authenticated HTTP download returns the original bytes and rejects later
  corruption through the same API.

## Scope and limits

This is a read-admission and concurrency improvement, not complete filesystem
snapshot isolation. FileResponse opens the path after verification: a privileged
or external local writer can still mutate/replace it during that gap or streaming.
Owned-media storage must remain write-controlled. Render and export verification
are unchanged, including prior exact-frame and SDR-color fixes. Coalescing
reduces duplicate simultaneous hash work, not measured end-to-end latency or
an overall performance multiplier. No browser, Windows, HDR, or GPU certification
is implied.

Rollback: revert this checkpoint; no persistent-schema or media migration exists.

## Verification evidence

On the local Linux software-FFmpeg runtime:

- Read verification + linked frame split + domain: **83 passed** (before the
  additional authenticated HTTP byte-read test).
- Final read-verification suite including HTTP: **11 passed**.
- Commands use `PYTHONPATH=command-center:bossman-core:.` and the existing
  `/tmp/bossman-epoch4-venv/bin/python -m pytest` interpreter.
- Integration + real rendering + exact CFR regression: **70 passed, 3 skipped
  in 222.46 seconds**. Skips: optional host CV interpreter, downloaded Whisper
  model, and explicitly enabled local adapter integration probe. Prior SDR
  colors, endpoint frames, preview/export paths and authenticated operations
  remain passing. No new test skips or expected failures were introduced.
- `git diff --check` passed.
