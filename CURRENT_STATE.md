# CURRENT STATE

Canonical branch: **`release/bossman-owner`**. Canonical convergence PR: **#67**.

This file is a navigation pointer, not a certificate. Documentation preparation for the 21 September 2026 owner run does not declare the software ready.

- [Install the chosen Windows artifact](INSTALL.md).
- [Owner first-run protocol: HW-01…HW-13](OWNER_ACCEPTANCE.md).
- [Final integrator instruction](CLAUDE_NEXT_ACTION.md).
- [Known limitations](KNOWN_LIMITATIONS.md).
- [Historical capability decisions](CONVERGENCE_DECISIONS.md).
- [Salvage ledger](docs/final/FINAL_SALVAGE_LEDGER.md).

Authoritative release evidence identifies TESTED_SHA, executed mandatory workflow/job results, artifact source SHA and the SHA-256 of the exact distributed ZIP. Latest remote HEAD is the development tip; it is not automatically the tested install target. A later commit does not invalidate evidence for the older bytes, but cannot inherit it.

Source/unit tests, installed-product tests, real-model tests and owner-hardware tests are separate evidence levels. Mocks, missing jobs, cancelled/skipped/action_required runs and old reports never become PASS by relabeling.

Owner-only checks include the exact local model/runtime on the owner's Windows hardware, actual desktop interaction, authorized Telegram delivery and human-only login checkpoints. Missing code remains a software blocker. No new models, plugins, self-modification or live-trading authority are enabled by this documentation change.

Previous verbose entrypoint documents remain in the [pre-cleanup archive](docs/archive/owner-preflight-05a1bb02/README.md). The GitHub default branch is not changed by this cleanup; use the explicit canonical branch for this run.
