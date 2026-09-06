# Audit intake handoff — no competing database

Read before writing this package: the pinned TOTAL_LOCAL_ACCEPTANCE_20260906
specification; its OPEN_FINDINGS.json; audit-06-v5.md; AUDIT_STATE.md; the pinned
V4 plan and V5 scorecard; existing ASTRA acceptance collector; and the canary
rollback rehearsal. PR37 discussion returned no comments. Code search for the
intake endpoint returned incomplete results, so absence of an intake service
was NOT inferred.

This package is a JSONL handoff, not a new audit database or server. Import
`findings.jsonl` into the canonical existing intake once Opus resolves its schema.
Keep original finding IDs, discovered/reproduced SHA, fixed/verified SHA fields,
test selectors, raw logs and evidence tier. The six findings have not been
integrated or fixed here. `static-findings.json` is separate and must never be
counted as a runtime reproduction.

Do not reuse an old audit's `FIXED-AT01` label as proof of the new residual cases.
Do not copy the older missing-conflict-release diagnosis forward: the pinned
AdmissionKernel now includes settle/release bookkeeping. The durable replay
ledger was tested and passed; it is not a newly open replay finding. Statuses
from a4da0f8/6dfb1e9/0e8960a are evidence for those revisions, not this candidate.
