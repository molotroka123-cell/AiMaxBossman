# DEFECT LEDGER

## CONFIRMED_P0

None.

## CONFIRMED_P1

### B4 — browser download navigation failure

- Severity: P1
- Reproduction: direct navigation to a downloadable PDF through the product browser path; Playwright reports “Download is starting”, product returns HTTP 500, no file is created.
- Expected: download is handled, file appears in the safe download directory, Flight Recorder and UI report the outcome.
- Actual: unhandled download and no file.
- Affected subsystem: browser session/download handling.
- Evidence: `ver2/CP-03.md`, prior B4 evidence and current-truth handoff.
- Repair recommendation: explicitly handle Playwright download events and persist/verify the artifact before responding.
- Regression test: HTML navigation, direct PDF, attachment, click download, redirect download, TXT and ZIP fixtures.

## CONFIRMED_P2

### AP-ALL — approval list contract ambiguity

- Severity: P2
- Reproduction: after durable pending approvals exist, `GET /api/approvals?status=pending` returns them while `GET /api/approvals?status=all` returns `[]`.
- Expected: `all` returns all statuses or rejects the unsupported filter explicitly.
- Actual: empty response can mislead an operator into believing approvals disappeared.
- Affected subsystem: approvals API/UI query contract.
- Evidence: CP-04C A1–A3, approval ids 7–9 and read-only DB rows.
- Repair recommendation: implement explicit all-status semantics or rename/validate the filter.
- Regression test: create pending/rejected/approved rows and compare default, pending and all queries after restart.

### TEL-001 — benchmark telemetry methodology

- Severity: P2
- Reproduction: model UI reports 1.6/3.3 tok/s while native generation measures about 10/49 tok/s.
- Expected: UI metric reflects measured generation throughput or is clearly labeled as a different phase.
- Actual: misleading metric.
- Affected subsystem: model benchmark telemetry.
- Evidence: prior independent model benchmark and CP-06B.
- Repair recommendation: label prompt/evaluation throughput separately from TG tok/s.
- Regression test: compare UI, server timings and native benchmark on both Qwen endpoints.

## DISPROVED_FINDINGS

- Aster API approval disappearance as a repeatable product defect: disproved by A1–A3 in the same installed runtime/data store; rows survived restart and appeared in pending API results.
- Old memory B1 P1: not a confirmed defect; unique notes and approved agent memory effects passed.
- Simultaneous resident model thrashing: not observed with the two Qwen servers.

## MODEL_QUALITY_FINDINGS

- Qwen3.6 has an arithmetic reasoning caveat: one duplicate-file prompt returned 21 MB instead of the expected 14 MB. Qwen3.8 returned 14 MB.

## ENVIRONMENT_LIMITATIONS

- Computer Use exposed no native apps and no browser provider; fresh UI-only coding, media, music, UX torture and MVČR download exam could not be executed.
- Uncertain-outcome crash reconciliation was not forced in this safe pass.

## OWNER_REQUIRED

- MVČR flow must stop before BankID, Datová schránka, CAPTCHA, signature, payment, submission or external communication.
- No real user files, payments, wallets, trading, government submissions or third-party messages were used.

## ASTER_GLM_AGREEMENTS

- B4 downloadable PDF browser failure is P1.
- Native local Qwen performance is about 10/49 tok/s; UI benchmark is misleading P2 telemetry.
- Task-bound approval restart survives and effect executes exactly once.
- No confirmed old memory B1 P1.

## ASTER_GLM_DISAGREEMENTS

- Aster reported API-created approval loss after restart; this final controlled matrix did not reproduce it. DB rows and pending API results survived all three A cycles. Classification: Aster test/runtime anomaly unless a different data directory or API client contract is identified.
