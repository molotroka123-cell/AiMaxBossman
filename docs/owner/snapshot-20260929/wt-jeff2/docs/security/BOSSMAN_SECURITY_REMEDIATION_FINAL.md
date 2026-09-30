# BOSSMAN — SECURITY REMEDIATION FINAL (V2 freeze decision)

```
AUDIT_BASE_SHA=bb944d47864e70c3b93f01382e94f22dd59aeab5
FABLE_FIX_SHA=9ba0300c390a95f9b8eddbf494c68f24ea99bf83
FABLE_REPORT_SHA=09ab6160cf04719f149b444c95c202ca72818d17
POST_AUDIT_START_SHA=09ab6160cf04719f149b444c95c202ca72818d17
REMEDIATION_CHECKPOINT_SHA=1283894dc46b11d37534be373bde2c4e2edbb5ef
SESSION_START_SHA=3ec4c81d72b4930e1ac9006541ac7ebd8036ab6a
FINAL_SHA=4eb97a5 (last code commit; docs commit follows)
```

## What changed (per security boundary, one commit each)

| Boundary | Commit | Change |
|---|---|---|
| Host path authz (F-009/F-011) | aa67282, 341bbee | terminal roots for all modes, resolve-before-authz, session ownership, HTTP routes confined |
| Approval identity (F-013) | 043d3fa | digest over impl fingerprint + normalized args + context, recomputed at resume; registry collision guard |
| Verification (F-012) | fc903a2 | fresh-evidence verifier; text can veto, never approve; UNVERIFIED → human |
| Secrets (canary sweep) | 31edaab, 4eb97a5 | event payload redaction; approval/tool previews redacted; gateway RedactionFilter |
| Egress: http tool (F-004) | b0ed072 | scheme/host/resolve/redirect policy, fail-closed DNS |
| Execution path (F-005) | 6542273 | projects builtin allowlist + approvals; argv-only cmd templates |
| Data ≠ instructions (F-006/F-007) | 760ac6d, 4d792d7 | retrieved as user+header; every non-journal tool output marked; facts external with source |
| Cloud egress (F-008/F-016) | ec604ab, 5e389ff | gateway explicit-only header, embeddings gated, route-based audit, 429 bounded retry; cc router strict True, forced model checked, local derived from provider+host |
| MCP boundary (F-014) | 60ab250 | metadata sanitized/bounded/prefixed, collisions refused, spawn allowlist |
| Browser targets (F-010) | 4311621 | default-deny private/metadata/non-http, DNS check, post-goto re-check |
| Owner routes (F-015) | 341bbee | approval-by-record (consume once), actor restricted |
| Discovery/taskxchange (F-017, BUG-005) | 3d1e005 | URL policy with resolution, safe path segments, bounded probes |
| Dead code (F-018), BUG-004 | c10d36a | fileintel/analysis wired, deny-list + containment in code_index, context_os honest, pool bound to loop |

Full finding table with dispositions: `FABLE_51_FINAL_RETEST.md`. Attack/variant results:
`FINAL_ATTACK_MATRIX.md`. Machine-readable gates: `FINAL_SECURITY_GATES.json`.

## Evidence discipline

- Every FIXED row has a repro test that fails on pre-fix code paths (RED-first for A2/C2 work; PoC
  re-runs for F-001/002/003/012) and ≥1 adversarial variant.
- Nothing runtime-dependent is FIXED from mocks: subprocesses, files, SQLite rows, ASGI apps and
  asyncio servers are real. Docker/GPU/Windows-only proofs are NOT_TESTED_ON_THIS_HOST.
- Learning records (16 VERIFIED + 1 FAILED_EXPERIMENT) in `data/learning/` with independent
  verifiers (pytest/PoC re-runs, GLM 5.3 RunPod regression for F-001..003).

## Regression on FINAL_SHA

- bossman-core: 1360 passed, 5 skipped, 0 failed
- command-center: 718 passed, 3 skipped, 0 failed
- repo root: 35 passed, 0 failed
- compileall: OK · secret scan: PASS · PoCs: 3 blocked / 2 NOT_TESTED_ON_THIS_HOST (Windows junctions) / cc PoC v2 blocked

## Freeze verdict

```
SAFE_FOR_V2_FREEZE=YES
```

Rationale: all freeze blockers (F-009, F-012, F-013/F-014, F-006/F-007, F-008/F-016) are closed
with repro + variant tests on this host; no CRITICAL/HIGH remains open; no security regression;
cloud is fail-closed; verified success requires fresh evidence. What is still owner-dependent is
listed, not hidden: the docker RW-mount runtime proof for F-009 (the host-path authorization that
made the finding HIGH is fixed and proven; the container is now defense in depth), Windows/RunPod
re-runs for BUG-004 and the junction PoCs, and the accepted residual risks (DNS rebinding,
browser sub-requests, single-token HTTP authority). Those are gated as NOT_TESTED_ON_THIS_HOST /
ACCEPTED_RISK_REQUIRES_OWNER — they do not lower the gate; they name what the owner host must run
before tagging the release.
