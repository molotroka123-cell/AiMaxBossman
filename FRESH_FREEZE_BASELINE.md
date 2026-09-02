# FRESH_FREEZE_BASELINE — LONGHORIZON-FREEZE-001

mission_id: LFZ-20260902-GLM-7f13546
acceptance_id: LONGHORIZON-FREEZE-001
created: 2026-09-02 (UTC session)

## Repository state at mission start (fresh audit, not the old package audit)

- branch: `claude/bossman-control-v03-43igbk`
- start HEAD: `7f1354690e91f4a6d325fc27faab49ea1239e28a` (fetched ff from origin)
- package reference HEAD (old audit): `0d4d4feee3fb89ee1939814071669c79c348226a` — one commit below start HEAD (`7f13546` "Add files via upload" = ZIP3 ingest commit)
- tree at start: clean, synced with origin
- changes since package reference: ZIP3 upload commit only

## Environment fingerprint

- OS: Windows 11 (win32), PowerShell 5.1, git via winget
- Python: system 3.14.3; mission venvs: cpython 3.11.16 + 3.12.14 (uv-managed)
- bossman-core installed editable (`.[dev,resource]`) in both venvs
- pytest: `--basetemp` override required on this machine (stale `pytest-current` junction, WinError 5)

## CI workflows (fresh state)

| workflow | trigger | status at baseline |
|---|---|---|
| bossman-core-ci.yml | push/PR, py3.11+3.12 matrix, groups security/gateway-context/stage8-14/rest + compile | rest group was RED at reference audit: CI-HISTORY-001 (shallow checkout cannot resolve historical SHA 8a13f1d for `run_isolated`) |
| bossman-benchmark.yml | PR (SMOKE+PR), manual dispatch with owner/budget attestations | checkout shallow (same class of issue, fixed alongside) |
| bossman-v2-repair.yml | push/PR | CI-AUTOREPAIR-REPORT-001 (false success claim on failure PR) + REF-001 (candidate not tested) — both PRESENT at baseline |
| root-ci.yml | push/PR | green historically; workflow contract tests added this mission |

## Fixes produced this mission (atomic)

1. `5fad426` fix(ci): fetch-depth 0 (CI-HISTORY-001) — provenance checks unchanged; local sha_001_002 + sha_003 green before/after
2. `a21512f` fix(ci): honest auto-repair reporting + candidate-commit checkout (CI-AUTOREPAIR-REPORT-001/REF-001) + 2 contract tests

## Feature flags at baseline (bossman.apprentice.flags — ALL default OFF)

BOSSMAN_UNIVERSAL_COMPUTER_APPRENTICE, BOSSMAN_SKILL_RECORDING, BOSSMAN_SKILL_SHADOW_REPLAY,
BOSSMAN_SKILL_PROMOTION, BOSSMAN_CLAUDE_CODE_FALLBACK, BOSSMAN_EXTERNAL_OUTREACH,
BOSSMAN_APPRENTICE_DRY_RUN_PREVIEW, BOSSMAN_APPRENTICE_CHECKPOINT_RESUME,
BOSSMAN_APPRENTICE_ANCHOR_REDUNDANCY, BOSSMAN_APPRENTICE_LESSON_PRECHECK, BOSSMAN_APPRENTICE_EVIDENCE_EXPORT

Benchmark LIVE gate additionally requires BOSSMAN_BENCHMARK_OWNER_APPROVED=1 + BUDGET_RESERVED=1 + --allow-live.

## Initial blocker table

| ID | P | status |
|---|---|---|
| CI-HISTORY-001 | P0 | CLOSED (5fad426, pending fresh CI proof) |
| CI-AUTOREPAIR-REPORT-001 | P0 | CLOSED (a21512f + contract tests) |
| CI-AUTOREPAIR-REF-001 | P0 | CLOSED (a21512f + contract tests) |
| HIGGSFIELD-REAL | P1 | likely BLOCKED_BY_ENVIRONMENT (no credentials) — to verify honestly |
| TEACHER-REAL | P1 | feasible: claude_code_client.py present; budget reserved ($9 total, Fable share) |
| OUTREACH-REAL | P1 | feasible: public web research → WAIT_APPROVAL (no send) |
| RESTART-PROOF | P1 | durable store tests present (test_apprentice_live_safety.py) — to execute and record |

## Cloud budget

MAX_CLOUD_COST_USD = 9.00 (owner-authorized). Ledger: docs/mission/LONGHORIZON-FREEZE-001/ledger.jsonl
Token handling: secret provided in-session only; persisted flag claude_api_configured only. NEVER committed.
