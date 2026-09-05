# Repository truth

START_SHA=7b1377a4f69336a1ca9d8eb4d432a758e195ae33
REMOTE_HEAD_AT_FETCH=7b1377a4f69336a1ca9d8eb4d432a758e195ae33
FINAL_AUDITED_SHA=7b1377a4f69336a1ca9d8eb4d432a758e195ae33
BRANCH=claude/bossman-control-v03-43igbk
AUDIT_TIME_UTC=2026-09-05T15:22:07.499756+00:00
EXPECTED_RECENT_BASELINE=f657f12f2fd4d2bb27f1cad1c0910a5d8cd093a9

The actual head adds the Solana volume suite after the expected baseline. Commit messages were used for navigation only. Runtime data and uncommitted changes are not included in audited source. Initial checkout was clean; another task later edited Solana files, so an immutable detached worktree was created at the audited SHA. No production-source changes are part of this audit. Pushing the report creates a new report commit and does not change the source SHA to which these findings apply.

Repository layout: Core Python service uses PostgreSQL/Redis and model gateway; Command Center is a separate FastAPI/SQLAlchemy/UI application; V3 is Python ports and synchronous orchestration layered over existing adapters, with file journal and SQLite Organization/Fleet stores. Feature flags default V3/Organization/Fleet off. Shared evidence and learning libraries bootstrap from repository root. Existing deterministic cross-layer tests use local execution fixtures, not an authenticated remote fleet.

| Claimed component | Classification at source snapshot | Evidence and limits |
|---|---|---|
| V2 Action Truth / Command Center | INTEGRATED | engine completion hooks and structured verification; legacy Core differs |
| TaskJournal | IMPLEMENTED / BROKEN edge cases | ASTRA-002/003/005; flags and crash persistence |
| FailureMemory | IMPLEMENTED | JSON store; atomicity/corruption and retrieval limitations |
| ContextAssembler | IMPLEMENTED / BROKEN bounds | header budget and token estimator probes |
| CompoundRunner | INTEGRATED / BROKEN edge cases | Organization bridge and local E2E; ASTRA-002/005 |
| V3→V2 adapters | INTEGRATED | CommandCenter observer/verifier and tool execution; selected tests |
| Organization Layer | INTEGRATED | gated /api/org entry; planner/team/runtime/store; mission identity/review gaps |
| Fleet OS local | INTEGRATED | local logical-node transport, leases/queue/flight recorder; selected deterministic tests |
| Fleet authenticated remote transport | ABSENT | RemoteNodeTransport raises NotImplementedError |
| Scheduler / leases / queue / resume / privacy | IMPLEMENTED / BROKEN boundaries | sink fencing, crash ambiguity and joint resource admission gaps |
| Signed evidence | INTEGRATED | HMAC and trusted signers; no proof freshness/attempt binding at contract boundary |
| Passive Benchmark Overlay | INTEGRATED | local cross-layer export; unexercised dimensions can score max |
| Live README Scorecard | IMPLEMENTED | update script and CI check; not independent deployment attestation |
| OpenRouter provider path | INTEGRATED | env model setup and fake tool-loop test; real call NOT_RUN |
| Organization product entry | INTEGRATED | flag-gated API and V2 task binding; full mission UI projection PARTIAL |
| Control-plane endpoint | IMPLEMENTED | snapshots and flight events; owner usability/browser QA NOT_RUN |
| Cross-layer E2E | VERIFIED in selected local tests only | positive fresh file state and negative missing-effect case |
| Secret scanner 2.0 | IMPLEMENTED / PARTIAL | source and ZIP patterns, entropy; current exact-SHA checks fail |
| Login rate limiting | IMPLEMENTED | per-process IP limiter; distributed enforcement not claimed |
| Solana dashboard | IMPLEMENTED simulation path at baseline | mock signatures and synthetic loop; no live transaction attestation |

All classifications describe this evidence snapshot. No subsystem is ATTESTED. Complete repository-wide dead-code reachability and every possible domain-specific success writer are UNPROVEN. Principal execution paths and all ten required axes were reviewed; exhaustive lexical search results are retained separately.
