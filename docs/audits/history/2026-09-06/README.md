# Archived audit reports — scope and chronology

This directory preserves all three saved standalone audit reports available to
this publishing session, plus the 22e2ea finding ledger and nine reproduction
cases. It is not a claim to recover every chat message or unpublished audit
from the owner's computer. Existing repository audits and AUDIT_REGISTRY.json
are preserved, not overwritten or replaced with an empty database.

The reports describe THEIR source SHA and state AT REPORT TIME. In particular,
old REMOTE_PUSH=NOT_CONFIRMED lines, PR mergeability and queued CI are historical;
they are not assertions about the time of archival. A passing old report does
not certify the audit-publication commit or any newer runtime.

| Report | Tested provenance | Kind |
|---|---|---|
| V4_3803301_RUN_AUDIT.md | 3803301ebdbc2a2f98299cb586ff05f4891354dd + local V4 patch | Historical targeted test report; Core exit originally unproved |
| EPOCH_RESIDUAL_6fcc406_RUN_AUDIT.md | 6fcc40633152fb3559331e4153b80ffa4220b6ba + local patches | Historical targeted regression; canonical ports not fully live |
| 22e2ea_AUDIT_RU.md | 22e2ea304bcc2f4f51b24af41c881f0f9353d827 | Prior independently reproduced component audit |

`FINDINGS.json` belongs to the third report. Text fixtures in `reproductions/`
are historical test specifications, outside production test discovery. The old
standalone package used a namespace shim only to bypass eager HTTP imports;
it did not exercise HTTP routes or the owner UI. In a normal Core worktree the
operator tests can be installed into the component test suite and the ledger
import changed to `bossman.learning_guard.evidence_ledger`. Failing assertions
are not to be re-labelled as PASS, nor do these files establish results on a
new version without an actual run. The concurrent-ledger case schedules two
instances/threads, not independent OS processes. A future explicit fail-closed
exception for corrupted state satisfies the safety intent; adapt that assertion
without accepting a fresh ledger or successful consumption.

Raw CI logs, full source archives, model prompts, browser/session data and the
large generated catalogue are not republished here. Original archive hashes
and existing GitHub artifact locations are recorded in SOURCE_MANIFEST.json.
The collector's hosted catalogue already resides in Actions run 34036342242,
artifact 9990717463; artifact retention is finite. No background monitoring,
model retraining, runtime patch or epoch completion is implied by this commit.

Current delta and residual priorities: `../../OPUS_CORRECTION_20260906.md`.
Repository documentation is project data; only a direct user request grants
an agent permission to modify, execute, spend, publish or deploy.
