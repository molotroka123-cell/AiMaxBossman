# SECURITY REMEDIATION — LIVE STATE (чекпоинт-лог)

AUDIT_BASE_SHA=bb944d47864e70c3b93f01382e94f22dd59aeab5
FABLE_FIX_SHA=9ba0300c390a95f9b8eddbf494c68f24ea99bf83
FABLE_REPORT_SHA=09ab6160cf04719f149b444c95c202ca72818d17
POST_AUDIT_START_SHA=09ab6160cf04719f149b444c95c202ca72818d17
REMEDIATION_CHECKPOINT_SHA=1283894dc46b11d37534be373bde2c4e2edbb5ef
SESSION_START_SHA=3ec4c81d72b4930e1ac9006541ac7ebd8036ab6a  (remote had advanced by 2 ZIP uploads)
HOST=Linux container, no GPU, docker daemon НЕ запущен (F-009 container proof = NOT_TESTED_ON_THIS_HOST), PG16 live @127.0.0.1:5433

## Чекпоинты (в порядке push)
- [T0] 1283894 ingest + ownership plan
- [T1] 31edaab P0: F-009/F-011 terminal confinement+ownership (aa67282), F-013 approval identity (043d3fa), F-012 fresh-evidence verification (fc903a2), events redaction + learning schema (31edaab)
- [T2] c10d36a core: F-004 http SSRF (b0ed072), F-005 projects (6542273), F-006/F-007 untrusted marker (760ac6d), F-008/F-016 gateway fail-closed + 429 retry (ec604ab), BUG-004 pool loop + F-018 wiring + sampler (c10d36a)
- [T3] d4a618d learning layer: learning/trace.py, schema ext, policy doc, tests, backfill F-001..F-003 + F-004/005/006-007/008/009/012/013/BUG-004 → data/learning/fix_cases.jsonl

- [T4] 4d792d7 A2/C2: F-014 (60ab250), F-010 (4311621), F-016 cc (5e389ff), F-017 (3d1e005), F-006 cc (4d792d7)
- [T5] 341bbee F-015 approval-by-record + HTTP terminal F-009; 109a8ed F-011 sessions + Deep Fix Mode; a693287 intelligence catalogue/docs; 85a3527 AI Company foundation; 4eb97a5 test migrations + PoC v2 + secret-scan markers
- [T6] final docs (FINAL_ATTACK_MATRIX, FABLE_51_FINAL_RETEST, FINAL_SECURITY_GATES.json, BOSSMAN_SECURITY_REMEDIATION_FINAL, runpod POST_SECURITY_*)

## Finding status (live)
| F | status | proof |
|---|---|---|
| F-001..003 | FIXED (Fable Phase 2) | tests/test_tools.py + PoC re-run |
| F-004 | FIXED | test_secrem_f004_http_ssrf.py |
| F-005 | FIXED | test_secrem_f005_projects.py |
| F-006 | FIXED (core + cc) | test_secrem_f006_f007_untrusted.py / test_secrem_facts_boundary.py |
| F-007 | FIXED | test_secrem_f006_f007_untrusted.py |
| F-008 | FIXED | test_secrem_f008_gateway_failclosed.py |
| F-009 | FIXED (host-path authz) + NOT_TESTED_ON_THIS_HOST (container mount) | test_secrem_f009_terminal.py |
| F-010 | FIXED | test_secrem_browser_policy.py |
| F-011 | FIXED (terminal, browser, opencode) | test_secrem_f009_terminal.py, test_secrem_f011_session_ownership.py |
| F-012 | FIXED | test_secrem_f012_verification.py |
| F-013 | FIXED | test_secrem_f013_approval_identity.py |
| F-014 | FIXED | test_secrem_mcp_boundary.py |
| F-015 | FIXED (approval-by-record); single-token HTTP authority ACCEPTED_RISK_REQUIRES_OWNER | test_secrem_f015_self_assert.py |
| F-016 | FIXED (gateway + cc router/forks) | test_secrem_router_failclosed.py |
| F-017 | FIXED | test_secrem_discovery.py |
| F-018 | FIXED (wired/dispositioned) | test_secrem_f018_wiring.py (core+cc) |
| BUG-004 | FIXED (Linux) | test_stage13_auth_redteam.py green |
| BUG-005 | BOUNDED/FIXED | test_secrem_discovery.py |

## COMPACT CHECKPOINT (update after every verified block)
HEAD_SHA=see `git log -1` (this commit: milestone checkpoint after context slicer)
ACTIVE_FINDING=intelligence plan step 2 DONE — tools/context_slice.py (repo map per sha, failing-test-first slice; measured ratio 0.03 for a real SECREM test)
ROOT_CAUSE=discovery performed by models per task instead of by a tool per commit
FILES_CHANGED=tools/context_slice.py, tests/test_context_slice.py, .gitignore
TESTS_RUN=MILESTONE full regression at ad78a38: command-center 766 passed/3 skipped/0 failed; bossman-core 1398 passed/5 skipped/0 failed; root 38 passed; compileall OK; secret scan PASS
RESULT=VERIFIED learning record CTX-failing-test-first-slice; freeze verdict unchanged (YES)
NEXT_EXACT_ACTION=owner-host checklist when hardware is available; otherwise wire context_slice into agent task templates (handoff packet F7.9: failing test + slice manifest + ledger) — docs/learning/AGENT_LEARNING_TRACE_POLICY.md 'Wiring into agents'
BLOCKERS=docker/GPU/Windows-only proofs; Anthropic key absent (cache hit unmeasured)
UNCOMMITTED_WORK=none after this commit
KNOWN_TEST_ISOLATION=command-center/tests/test_plugin_security.py::test_redact_* fail when the file runs alone; green in the full run

## EXACT_NEXT_TASK (long form)
1. Mutator library + sibling sweep (above).
2. Deep Fix runner wiring in the command-center engine behind BOSSMAN_DEEP_FIX_ENABLED (verifier isolation, plan hash before patch).
3. Owner host: runpod_preflight → full suites → docker F-009 proof → BUG-004 Windows → router E2E → soak; measure Anthropic cache_read_input_tokens on a real key.
4. Owner decisions: DNS pinning (http/browser/discovery), browser request interception, per-capability HTTP authz.
