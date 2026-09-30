# Checkpoint response — byte-faithful context manifests

Source: PR26, checkpoint `30fce6c44865ab2ca064644a6c0e21fcd5be4551`,
`docs/acceptance/20260906-kimi-779bb44/failures.md`, BUG-003.
This checkpoint was already published; no claim of a new arrival during the fix.
Main inspected before editing: `2919b8319c8da512c5a54408c35db04bea858f07`.
Its archive hash and reconstructed Git tree matched the published snapshot:
`82fbfe3e204d07d34b97a5208dd47953b11b492be2333b8b40b345892102429b`,
`f1b106aa5f026f820c72a30e08e71961867c42cc`.

## Fixes carried to the main candidate

- Both repo_map and failing_test_slice hash the exact file bytes. Replacement
  decoding and universal-newline normalization remain analysis-only. Symbols,
  token estimates and map digest share one file read.
- Version the derived repo-map cache. Legacy text-digest entries cannot survive
  under an unchanged cache key; they rebuild and subsequent calls hit the cache.
  Corrupt derived caches rebuild; this rule never applies to evidence journals.
- The pre-existing Windows assertion now compares the hash of the actual bytes.
- Separate link-retarget coverage from add/edit/delete/rename coverage. Only an
  actual Windows symlink privilege error (1314) skips the link case; unrelated
  OS errors still fail and ordinary fingerprint cases still run.
- Regenerate the existing skip registry against the actual source. No coverage,
  verifier, approval, privacy or acceptance threshold is lowered.

## Evidence and limits

New cross-platform byte fixtures reproduced **7 failed / 3 passed**, exit 1,
including CRLF, mixed line endings, invalid UTF-8 and a legacy cache entry.
After correction: context byte regressions + existing context tests + skip
registry: **23 passed**, exit 0, 27.99 s. Final byte-matched transfer rerun:
**23 passed**, exit 0, 27.74 s, Linux/Python 3.13.5.
One earlier combined attempt was terminated by the command's 45s deadline after
22 progress dots; no exit/JUnit was captured, so it is NOT PASS. The complete
rerun above is the accepted local result. No failures were converted to xfail.

This is targeted code/tool evidence, not a full root/Core/CC regression,
Windows UI, local-model benchmark or production human-speed acceptance.
Explicit caller-provided cache keys retain their documented override behavior;
this does not solve every source-change race or make a manifest authorization.
BUG-002's earlier restart failures were reported resolved after eliminating
cross-worktree imports; those stale logs are not grounds for another runtime fix.
BUG-001 CFR duration and all unrelated findings remain separate reconciliation
items. Do not widen duration tolerances or gates merely to make old tests pass.

The owner's active test worktree, installed application and model processes
were not changed. A repository push does not hot-update that running version.
No automatic background watcher, AI swarm or delayed coding job was installed.

VERDICT=TARGETED_CONTEXT_FIX_LOCALLY_VERIFIED
CURRENT_SHA_CI=PENDING_AFTER_PUSH
HUMAN_SPEED=NOT_MEASURED
V4_V5_RELEASE=NOT_ACCEPTED
