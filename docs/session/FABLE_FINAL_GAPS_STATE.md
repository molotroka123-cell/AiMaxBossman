# FABLE FINAL GAPS — session checkpoint (read this first)

CURRENT_HEAD=(see git log; PASS1 commit on top of 7fc4343)
COMPLETED_PASS=PASS1 benchmark truthfulness
STATUS=PASS1 committed; PASS2 not started
FILES_READ=benchmark/{__init__,cli,engine,fixture_runtime}.py, datasets/v1/manifest.json, tests/test_benchmark_runtime.py, bossman-benchmark.yml, apprentice/{durable,live_workspace,claude_code_client}.py (signatures + key methods)
FILES_CHANGED=benchmark/engine.py, cli.py, sandbox_runtime.py (new), datasets/v1/manifest.json (1.1.0), tests/test_benchmark_runtime.py, tests/test_benchmark_truth.py (new), docs/autonomy/AUTONOMY_LEARNING_BENCHMARK.md
TESTS_RUN=cd bossman-core && python -m pytest tests/test_benchmark_runtime.py tests/test_benchmark_truth.py -q --timeout=600
TEST_RESULTS=see git log -1 message (post-commit run)
ACCEPTANCE_IDS=BENCH-MODE-001 PASS, BENCH-MODE-002 PASS, BENCH-SHA-001 PASS, BENCH-SHA-002 PASS, BENCH-SHA-003 PASS, BENCH-PROVENANCE-001 PASS (post-commit)
KNOWN_BLOCKERS=no docker daemon on this host; no bwrap; no Anthropic/Claude executable; no Higgsfield session; no Google Maps live
NEXT_PASS=PASS2 hermetic Claude Code teacher (sanitized temp workspace with bundle files only, diff out, verifier worktree, LiveWorkspace refuses protected writes directly)
NEXT_FILES=bossman-core/bossman/apprentice/claude_code_client.py, live_workspace.py, teacher.py (build_bundle/PatchVerifier only), tests/test_apprentice_live_safety.py
NEXT_TESTS=bossman-core/tests/test_apprentice_live_safety.py, bossman-core/tests/test_teacher_isolation.py (new)
EXACT_NEXT_COMMAND=cd bossman-core && python -m pytest tests/test_apprentice_live_safety.py tests/test_teacher_isolation.py tests/test_apprentice_teacher.py -q --no-header -p no:cacheprovider -o addopts="" --timeout=300

## Verified facts at 7fc4343 (existence checks only)
- jsonschema dev dependency present in bossman-core/pyproject.toml [dev]
- bossman/apprentice/durable.py (132 lines), claude_code_client.py (88), live_workspace.py (106) exist
- bossman/benchmark/{engine,cli,fixture_runtime}.py exist; .github/workflows/bossman-benchmark.yml exists (PR/manual only)
- docs/autonomy/AUTONOMY_LEARNING_BENCHMARK.md + benchmark_history exist

## Remaining passes
PASS1 benchmark truth · PASS2 hermetic teacher · PASS3 durable LIVE + owner auth · PASS4 real E2E (GUI/Claude/outreach, BLOCKED honestly) · PASS5 FrontierBench v2 + auditor · PASS6 BEST decision inventory + evidence registry · PASS7 release gate + freeze report
