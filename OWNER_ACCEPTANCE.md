# BOSSMAN OWNER ACCEPTANCE

Canonical line: `release/bossman-owner`.

The release is owner-ready only when one exact SHA has:
- clean Windows install outside the repository checkout;
- mandatory exact-SHA CI complete;
- installed-product owner scenarios complete;
- zero software-fixable P0/P1;
- no unexplained dead UI control or UI→API mismatch;
- an owner-hardware pack for the remaining physical/account-bound checks.

## Owner flow

1. Install the exact Windows artifact.
2. Complete first boot until the UI says READY or lists concrete items requiring attention.
3. Run the installed `bcc.owner_acceptance` harness.
4. Execute HW-01…HW-12 from `tests/owner_hardware/manifest.json`.
5. Keep real Telegram, local-model, real desktop-control and MVČR submission checks as OWNER_ACTION_REQUIRED until performed on the owner's machine/account.
6. Do not treat mocks, source imports, old-SHA runs, skipped jobs or clicks without verified effects as PASS.

The final certificate must name TESTED_SHA and the Windows artifact SHA-256.
