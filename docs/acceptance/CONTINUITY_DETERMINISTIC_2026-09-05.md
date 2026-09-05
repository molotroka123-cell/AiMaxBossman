# Deterministic continuity acceptance — 2026-09-05

Scope: existing continuity, Organization, Fleet, memory, and routing suites. Local execution only; no provider credentials accessed and no paid calls. No production code changed. Evidence below is deterministic test execution, not live model acceptance.

## Results

| Run | Result | Interpretation |
| --- | --- | --- |
| Core continuity subset | 45 passed, 1 failed; 7.74 s; exit 1 | Organization resume test reached successful completion and exactly three writes, then failed reading its UTF-8 journal through Windows cp1252. |
| Focused failed-test retry plus Command Center subset, UTF-8 enabled | 14 passed, 3 setup errors; 0.91 s; exit 1 | Failed core test now passed. Mixed-project invocation did not load Command Center asyncio configuration; its three async tests failed during setup. |
| Command Center subset with explicit project configuration | 16 passed; 1.55 s; exit 0 | All requested Command Center tests passed with their intended pytest configuration. |

Final coverage: **62 distinct tests passed** across the corrected invocations (46 core, 16 Command Center). Initial failures remain recorded; this does not claim one clean combined run or completion of live acceptance. Failure classes: TOOLING / test portability and TOOLING / invocation configuration. Neither failure implicates a model. Retry strategies changed in response to exact evidence; no identical failing invocation was repeated.

## Exact commands

Working directory: `C:\AiMaxBossman-claude-bossman-control-v03-43igbk`.

Run 1:

```powershell
$env:PYTHONPATH = "$PWD\bossman-core;$PWD\command-center;$PWD"
& .\.venv-ci312\Scripts\python.exe -m pytest -q bossman-core/tests/test_v3_compound_resume.py bossman-core/tests/test_v3_memory_kernel.py bossman-core/tests/test_v3_organization_e2e.py bossman-core/tests/test_v3_fleet_e2e.py bossman-core/tests/test_v3_fleet_safety_proofs.py
```

Run 2:

```powershell
$env:PYTHONPATH = "$PWD\bossman-core;$PWD\command-center;$PWD"
$env:PYTHONUTF8 = '1'
& .\.venv-ci312\Scripts\python.exe -m pytest -q bossman-core/tests/test_v3_organization_e2e.py::test_restart_resumes_without_duplicate_side_effects command-center/tests/test_feat_organization.py command-center/tests/test_feat_router.py command-center/tests/test_model_intelligence.py
```

Run 3:

```powershell
$env:PYTHONPATH = "$PWD\bossman-core;$PWD\command-center;$PWD"
$env:PYTHONUTF8 = '1'
& .\.venv-ci312\Scripts\python.exe -m pytest -c command-center/pyproject.toml -q command-center/tests/test_feat_organization.py command-center/tests/test_feat_router.py command-center/tests/test_model_intelligence.py
```

## Verified deterministic behavior

- `bossman-core/tests/test_v3_compound_resume.py`: first-unfinished-step resume, changed model label with no verified-step replay, completed-chain idempotency, failed verification blocks parent completion. Model labels here do not invoke providers.
- `bossman-core/tests/test_v3_memory_kernel.py`: persistent receipts, model-independent resume context, failure memory, bounded context, provenance, secret redaction.
- `bossman-core/tests/test_v3_organization_e2e.py`: delegation and independent review, false-success rejection, restart/approval continuation without duplicate sandbox writes, department-budget blocking.
- `bossman-core/tests/test_v3_fleet_e2e.py`: Organization-to-Fleet execution on two logical in-process nodes, node-loss recovery under mission `m1`, exactly one execution of each verified sandbox write, privacy enforcement, irreversible-step uncertainty blocks replay.
- `bossman-core/tests/test_v3_fleet_safety_proofs.py`: lease and placement safety; explicitly proves remote transport unavailable.
- Command Center suites: Organization feature gates and HTTP routes, registry-backed routing, capability/scoring foundations. Organization enabled-route fixture executed; no skips reported.

## NOT_VERIFIED: acceptance gaps

- **Live GLM/provider hot-swap:** no provider call in these tests. Automatic exhaustion classification, dynamic free-model discovery, and GLM-to-free continuation remain NOT_VERIFIED.
- **All six hot-swap assertions together:** deterministic tests cover same mission and duplicate prevention, but do not independently establish preservation of permissions, constraints, and the acceptance-wide USD 3 budget through a real model switch. NOT_VERIFIED.
- **Fleet in Command Center:** `command-center/bcc/features/control_plane.py:99-100` explicitly reports Fleet disabled and not wired into Command Center. Python API tests do not prove live UI routing. NOT_VERIFIED.
- **Gateway credit failover:** `bossman-core/bossman/gateway/backends.py:16-32` recognizes 408/425/429/5xx/transport for failover, excluding HTTP 402. Whether this path is used by the live acceptance mission and how credits are handled remain NOT_VERIFIED.
- **Resume task projection:** `OrganizationService.run_mission()` synchronizes V2 task statuses, while `.resume()` does not (`command-center/bcc/features/organization.py:137-150`). Possible stale V2 task/run display requires reproduction; NOT_VERIFIED, not a confirmed defect.

Reusable verified procedure: use Python UTF-8 mode for these existing Windows tests and select each project's pytest configuration explicitly. The encoding-sensitive test itself remains unchanged and can still fail under cp1252.
