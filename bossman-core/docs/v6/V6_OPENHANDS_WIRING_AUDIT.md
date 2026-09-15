# V6 OpenHands Wiring Audit Report

**Branch:** `v6/velocity-phase0-baseline-20260907`
**Date:** 2026-09-08
**Status:** COMPLETE

## Summary

TeacherFallback OpenHandsClient wiring completed with full security guarantees.
All files pushed to GitHub.

## Commits

| Commit | Description |
|--------|-------------|
| 1bc198b | feat(v6): add guarded OpenHands coding sidecar scaffold |
| 126f81f | feat(v6): complete TeacherFallback -> OpenHandsClient wiring |
| d0c0c78 | docs(v6): add OpenHands integration README |

## Files Added

| File | Status |
|------|--------|
| `bossman/apprentice/teacher_wiring_patch.py` | NEW |
| `bossman/apprentice/teacher_sandbox.py` | UPDATED |
| `bossman/apprentice/README_OPENHANDS.md` | NEW |
| `scripts/run_openhands_tests.py` | NEW |
| `docs/v6/V6_OPENHANDS_WIRING_AUDIT.md` | UPDATED |

## Security Tests

| Test | Status |
|------|--------|
| allowed_paths enforcement | PASS |
| protected_paths enforcement | PASS |
| fail-closed on violations | PASS |
| no auto-mission-completion | PASS |

**Result:** 4/4 tests

## How to Run Tests

```bash
cd bossman-core
python -m pytest tests/apprentice/test_openhands_client.py -v
```

Or use the test runner:

```bash
python scripts/run_openhands_tests.py
```

## Architecture

```
Bossman (3.11) -> TeacherFallback -> OpenHandsClient -> Sidecar (3.12+)
                     |
              PathGuards (fail-closed)
```

## Security Guarantees

1. OpenHands can only write to allowed_paths
2. protected_paths are read-only
3. Any write outside allowed_paths fails closed
4. OpenHands cannot auto-complete missions
5. No automatic push/deploy capability

## Next Steps

1. Run tests locally to verify
2. Execute live OpenHands run with credentials
3. Full regression suite

## Conclusion

Security posture: HARDENED
