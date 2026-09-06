# Prepared Continuity delivery — 2026-09-06

The owner's offline V4 package is now published as hash-bound transport on
`fix/astra-v4-continuity-boundary-20260906`, not as a replacement of the active
UI candidate. The three payload files encode JSON line edits; the decoder checks
the JSON SHA256 and every file pre/postimage, including absence of newly added
paths. It never downloads or executes commands from the payload. A read-only
validation job exercises actual reconstructed code. Only after success may a
separate publish job commit the identical postimages to this isolated branch
without force. A moved branch or changed preimage stops delivery.

Scope: 12 remaining files above f925d3d (which already holds the action snapshot
and nine effect-boundary regressions). This imports existing prepared work:
mission DAG/lifecycle/worker admission, canonical full-action approvals, compound
receipts, UI dependency state and regression tests. No ObjectiveStore/admission
files overlap PR #25. No active tester worktree or main/integration ref is changed.

`CONTINUITY_PATCH_RUN_20260906.md` in the prepared patch is the ORIGINAL historical
offline run audit. Its offline push-failure/teardown status must not be mistaken
for the delivery status. The later offline run logged normal Core exit; the
current GitHub validation's exit code and artifact remain the authority for this
reconstruction. Delivery and validation commits differ; no full exact-SHA CI,
Windows/hardware/live UI/real-model retention or epoch-completion claim follows.

A failed/queued delivery job means PREPARED_PATCH_PUBLISHED, NOT runtime delivered.
The final bot commit must be fetched and verified separately. All scaffolding
created by this delivery removes itself only on successful publication.
