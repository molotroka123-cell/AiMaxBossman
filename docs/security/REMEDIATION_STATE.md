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
- [T3] learning layer: learning/trace.py, schema ext, policy doc, tests, backfill F-001..F-003 + F-004/005/006-007/008/009/012/013/BUG-004 → data/learning/fix_cases.jsonl

## Finding status (live)
| F | status | proof |
|---|---|---|
| F-001..003 | FIXED (Fable Phase 2) | tests/test_tools.py + PoC re-run |
| F-004 | FIXED | test_secrem_f004_http_ssrf.py |
| F-005 | FIXED | test_secrem_f005_projects.py |
| F-006 | FIXED (core) / IN_PROGRESS (bcc facts, agent C2) | test_secrem_f006_f007_untrusted.py / test_secrem_facts_boundary.py |
| F-007 | FIXED | test_secrem_f006_f007_untrusted.py |
| F-008 | FIXED | test_secrem_f008_gateway_failclosed.py |
| F-009 | FIXED (host-path authz) + NOT_TESTED_ON_THIS_HOST (container mount) | test_secrem_f009_terminal.py |
| F-010 | IN_PROGRESS (agent A2) | test_secrem_browser_policy.py (RED until done) |
| F-011 | FIXED (terminal ownership) / browser+opencode ownership OPEN | test_secrem_f009_terminal.py |
| F-012 | FIXED | test_secrem_f012_verification.py |
| F-013 | FIXED | test_secrem_f013_approval_identity.py |
| F-014 | IN_PROGRESS (agent A2) | test_secrem_mcp_boundary.py (RED until done) |
| F-015 | OPEN (self-asserted approved/actor flags) | — |
| F-016 | FIXED (gateway) / IN_PROGRESS (bcc router, agent C2) | test_secrem_router_failclosed.py |
| F-017 | IN_PROGRESS (agent C2) | test_secrem_discovery.py |
| F-018 | FIXED (wired/dispositioned) | test_secrem_f018_wiring.py (core+cc) |
| BUG-004 | FIXED (Linux) | test_stage13_auth_redteam.py green |
| BUG-005 | IN_PROGRESS (agent C2) | test_secrem_discovery.py |

## EXACT_NEXT_TASK
1. Collect agents A2 (F-014/F-010) and C2 (F-016 bcc/F-017/F-006 bcc/BUG-005): run their test files + related, commit per boundary, push.
2. F-015 self-asserted flags (bcc/features/terminal.py, browser.py, snapshot.py, nl_orchestra.py, api.py PATCH /agents) + F-011 browser/opencode session ownership → tests → commit.
3. Deep Fix Mode (flag OFF) + AI Company foundation (flag OFF) + intelligence ideas log.
4. Re-attack matrix (.agents/redteam/*.py + variants), full regressions, compileall, secret scan, final docs, verdict.
