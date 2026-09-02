# FABLE FINAL GAPS — session checkpoint (read this first)

CURRENT_HEAD=(git log -1; PASS2 commit on top of 8a90fce)
COMPLETED_PASS=PASS2 hermetic Claude Code teacher
STATUS=PASS1+PASS2 committed; PASS3 not started
FILES_READ=apprentice/{claude_code_client,live_workspace,teacher(build_bundle,AcceptanceBinding)}.py, tests/test_apprentice_live_safety.py (names)
FILES_CHANGED=apprentice/teacher_sandbox.py (new), claude_code_client.py, live_workspace.py, teacher.py (restore=True), tests/test_teacher_isolation.py (new)
TESTS_RUN=cd bossman-core && python -m pytest tests/test_teacher_isolation.py tests/test_apprentice_live_safety.py tests/test_apprentice_teacher.py tests/test_apprentice_e2e.py -q --timeout=300 → 43 passed, 1 skipped (live gated); BOSSMAN_TEACHER_LIVE_SMOKE=1 -k live_001 → 1 passed (real claude, 70 s)
TEST_RESULTS=PASS; TEACHER-LIVE-001 PASS with the real local `claude` (2 paid calls ≈ $0.06 each: first exposed the CLI result-envelope bug, second passed after the fix)
ACCEPTANCE_IDS=BENCH-* PASS (PASS1); TEACHER-ISO-001..005 PASS; TEACHER-LIVE-001 PASS (isolation level on this host: process-cwd+tool-denylist; bwrap absent)
KNOWN_BLOCKERS=no docker daemon on this host; no bwrap; no Anthropic/Claude executable; no Higgsfield session; no Google Maps live
NEXT_PASS=PASS3 durable LIVE (no durable store → refuse LIVE effects in the composition root) + owner-auth issuer/challenge bound to authenticated owner; persist consumption via DurableSafetyStore
NEXT_FILES=bossman-core/bossman/apprentice/guards.py, durable.py, outreach.py (OutreachGate ctor/send), engine.py (composition of ledger/registry), auth primitives found by grep (bossman/perimeter or auth module)
NEXT_TESTS=bossman-core/tests/test_durable_live_owner_auth.py (new), tests/test_apprentice_live_safety.py, tests/test_apprentice_outreach.py
EXACT_NEXT_COMMAND=cd bossman-core && python -m pytest tests/test_durable_live_owner_auth.py tests/test_apprentice_live_safety.py tests/test_apprentice_outreach.py -q --no-header -p no:cacheprovider -o addopts="" --timeout=300

## Verified facts at 7fc4343 (existence checks only)
- jsonschema dev dependency present in bossman-core/pyproject.toml [dev]
- bossman/apprentice/durable.py (132 lines), claude_code_client.py (88), live_workspace.py (106) exist
- bossman/benchmark/{engine,cli,fixture_runtime}.py exist; .github/workflows/bossman-benchmark.yml exists (PR/manual only)
- docs/autonomy/AUTONOMY_LEARNING_BENCHMARK.md + benchmark_history exist

## Remaining passes
PASS1 benchmark truth · PASS2 hermetic teacher · PASS3 durable LIVE + owner auth · PASS4 real E2E (GUI/Claude/outreach, BLOCKED honestly) · PASS5 FrontierBench v2 + auditor · PASS6 BEST decision inventory + evidence registry · PASS7 release gate + freeze report
