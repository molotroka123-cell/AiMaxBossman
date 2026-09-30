# POST-SECURITY FINAL ACCEPTANCE (RunPod / owner host)

Status on this container: security remediation complete and regression-green (see
`docs/security/BOSSMAN_SECURITY_REMEDIATION_FINAL.md`). GPU, docker and Windows-only proofs are
NOT_TESTED_ON_THIS_HOST and must be executed on the owner host / RunPod with the prepared harness.

## Run on the GPU host

```
python tools/runpod_preflight.py                       # environment/ports/models
cd bossman-core && python -m pytest tests -q            # expect 1360 passed, 5 skipped, 0 failed (+ GPU-only tests un-skipped)
cd command-center && python -m pytest tests -q          # expect 718 passed, 3 skipped, 0 failed
python tools/local_hardware_ab.py --help                # A/B harness (direct vs Bossman), sampler counts ollama tree
docker info && cd command-center && python -m pytest tests/test_secrem_f009_terminal.py -q   # container-mount proof no longer skipped
```

## Acceptance checklist

| Item | Expected | Status here |
|---|---|---|
| Security suites (`test_secrem_*`, stage12/13 red-team) | all pass | PASS (this host) |
| F-009 container RW mount confined to owner roots | docker test passes (not skipped) | NOT_TESTED_ON_THIS_HOST |
| BUG-004 auth red-team b3/b4/b7 | green on Windows/RunPod | FIXED on Linux; re-run pending |
| Router real E2E (SMALL/MEDIUM/LARGE, LARGE refusal, escalation benefit) | INTEGRATION_PROVEN → E2E_PROVEN with real models | NOT_TESTED_ON_THIS_HOST (fake adapters only) |
| Soak ≥100 mixed tasks | 0 approval bypass, 0 cloud calls under deny, 0 secret leaks | NOT_TESTED_ON_THIS_HOST |
| Secret canary `BOSSMAN_TEST_SECRET_9F31A7` sweep | never persisted/visible | PASS (events, approvals, previews, flight recorder, browser) |
| Cloud calls during regression | 0 real cloud requests | 0 (MockTransport; gateway fail-closed) |

Metrics template: `POST_SECURITY_METRICS.json`.
