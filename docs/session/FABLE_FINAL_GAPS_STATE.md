# FABLE FINAL GAPS — session checkpoint (read this first)

CURRENT_HEAD=(git log -1; PASS3 commit on top of 4212087)
COMPLETED_PASS=PASS3 durable LIVE + owner auth
STATUS=PASS1-3 committed; PASS4 not started
FILES_READ=apprentice/{guards,durable,outreach(OutreachGate),engine(ctor)}.py, remote_client/{security,auth(SCOPE_*, Principal)}.py
FILES_CHANGED=apprentice/durable.py (issued_approvals table), guards.py (live=True needs store; require_issued), outreach.py (mode SIMULATED|LIVE), owner_auth.py (new), composition.py (new), tests/test_durable_live_owner_auth.py (new)
TESTS_RUN=cd bossman-core && python -m pytest tests/test_durable_live_owner_auth.py tests/test_apprentice_live_safety.py tests/test_apprentice_outreach.py tests/test_apprentice_e2e.py tests/test_lead_uca_adversarial.py tests/test_apprentice_core.py -q → 62 passed
TEST_RESULTS=PASS
ACCEPTANCE_IDS=BENCH-* PASS; TEACHER-ISO-001..005 PASS; TEACHER-LIVE-001 PASS; DURABLE-LIVE-001..005 PASS; OWNER-AUTH-001..005 PASS
KNOWN_BLOCKERS=no docker daemon on this host; no bwrap; no Anthropic/Claude executable; no Higgsfield session; no Google Maps live
NEXT_PASS=PASS4 real E2E: (A) real Chromium via Playwright on a local page with the real apprentice engine; (B) real claude teacher bug A→B learning; (C) real public source (OSM Nominatim or other) → site issue → demo → WAIT_APPROVAL; BLOCKED_BY_ENVIRONMENT where honest
NEXT_FILES=bossman-core/bossman/toolkit/browser.py (API), computer_operator/adapters/browser.py, apprentice/engine.py (observer/actuator protocol), tests/fixtures/apprentice/sim.py (protocol reference), apprentice/teacher.py (TeacherFallback.request)
NEXT_TESTS=bossman-core/tests/test_e2e_real_gui.py, test_e2e_real_claude.py, test_e2e_real_outreach.py (new, gated by env; BLOCKED reasons recorded)
EXACT_NEXT_COMMAND=cd bossman-core && python -m pytest tests/test_e2e_real_gui.py tests/test_e2e_real_claude.py tests/test_e2e_real_outreach.py -q --no-header -p no:cacheprovider -o addopts="" --timeout=600 -rs

## Verified facts at 7fc4343 (existence checks only)
- jsonschema dev dependency present in bossman-core/pyproject.toml [dev]
- bossman/apprentice/durable.py (132 lines), claude_code_client.py (88), live_workspace.py (106) exist
- bossman/benchmark/{engine,cli,fixture_runtime}.py exist; .github/workflows/bossman-benchmark.yml exists (PR/manual only)
- docs/autonomy/AUTONOMY_LEARNING_BENCHMARK.md + benchmark_history exist

## Remaining passes
PASS1 benchmark truth · PASS2 hermetic teacher · PASS3 durable LIVE + owner auth · PASS4 real E2E (GUI/Claude/outreach, BLOCKED honestly) · PASS5 FrontierBench v2 + auditor · PASS6 BEST decision inventory + evidence registry · PASS7 release gate + freeze report
