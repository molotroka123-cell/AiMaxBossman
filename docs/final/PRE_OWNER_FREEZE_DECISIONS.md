# PRE-OWNER FREEZE DECISIONS

Canonical target: `release/bossman-owner`.

This file records the current release decisions that supersede older unresolved wording in historical audit documents. It does not convert missing live/hardware evidence into PASS.

## CONTROL-001 — coordinate fallback

**CLOSED FOR SOFTWARE RELEASE.** Semantic/accessibility/structured/visual targeting is primary. A coordinate-like CLICK/DOUBLE_CLICK/DRAG is rejected unless it has a non-empty named target and either:

- comes from the explicit `vision` source; or
- carries `args.coordinate_fallback=true`.

The fallback still requires confidence at or above `MIN_VISION_CONFIDENCE`. Raw planner coordinates are not a primary targeting path. Regression coverage: `bossman-core/tests/test_control_001_coordinate_fallback.py`; owner scenario OS-22 also proves raw coordinates are not accepted as the semantic target.

## SECURITY-001 — remote script execution

**CLOSED FOR SOFTWARE RELEASE: ASK, content-bound.** `curl ... | sh` remains an ASK operation rather than AUTO or unconditional DENY. Approval is bound to the fetched remote content digest; changed content after approval is refused and must be reviewed again. Clearly destructive filesystem commands remain DENY. Owner scenario OS-29 is the release evidence. No approval may be reused to execute different bytes.

## OS-64 — destructive Web Designer root operation

**CLOSED FOR SOFTWARE RELEASE.** Deleting `<html>`/`<body>` is treated as whole-site/document destruction. The server discovers the actual DOM target, requires explicit `destructive_root_ack`, a current base version and recoverable version history, and rejects stale/replayed approval. Recovery is byte-for-byte tested in `command-center/tests/test_web_designer_root_delete_boundary.py`.

## Image Studio product-path edit proof

**CLOSED FOR SOFTWARE RELEASE.** OS-16 drives the product transform path, persists and reopens the asset, proves bytes/pixels changed, and verifies export. AI image generation remains a separate provider/credential claim and a transformed fixture is never labeled as model generation.

## Telegram internal round-trip

**CLOSED FOR SOFTWARE RELEASE.** OS-15 covers approval creation, authenticated callback, one-time consumption and replay refusal through the product approval/Telegram boundary using a deterministic local transport. Real owner-account delivery remains owner-hardware/live-credential evidence.

## Intelligence Preservation

**INTENTIONALLY FAIL-CLOSED, NON-RELEASE EVIDENCE AXIS.** If same-model current benchmark evidence is not available, the measurement stays `INSUFFICIENT_EVIDENCE`. Do not create a synthetic `intelligence-preservation-current.json`, weaken the gate or transfer an older SHA's result.

## Human-speed Windows evidence

**NON-RELEASE EVIDENCE AXIS UNTIL MEASURABLE.** Windows hosted runners can expose a 15.625 ms quantized thread CPU clock, producing `degenerate_measurement`. Preserve the samples and fail closed; do not relabel that measurement PASS. Real visible-input/human-comparison evidence belongs to the owner hardware run.

## Release boundary

The current closure may declare `READY FOR OWNER TEST` only when the mandatory exact-SHA release workflows in `tools/exact_sha_certify.py` are completed successfully, the clean Windows artifact exists for that same SHA, software P0/P1 are closed, and owner-hardware cases are prepared. These decisions do not waive any mandatory release gate.
