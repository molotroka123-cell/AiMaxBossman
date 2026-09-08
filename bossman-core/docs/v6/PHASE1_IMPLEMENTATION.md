# Phase 1: Real Sidecar Protocol and OpenHandsClient

**Date:** 2026-09-08
**Status:** COMPLETE

## What Was Built

### 1. OpenHands Sidecar Script

**File:** `scripts/openhands_sidecar.py`

Real sidecar implementation with:
- `bossman.openhands.v1` protocol
- JSON request/response format
- Secret redaction in logs
- Structured error handling
- Timeout support
- Model configuration

### 2. OpenHandsClient Implementation

**File:** `bossman/apprentice/openhands_client.py`

Real client with:
- Sidecar process execution
- Path security validation
- Fail-closed on violations
- Independent evidence derivation
- Timeout handling
- Error categorization

### 3. Key Security Properties

1. **Path validation** - Handles `..`, absolute paths, mixed separators
2. **Fail-closed** - Any violation blocks execution
3. **Independent evidence** - Bossman derives Git evidence, doesn't trust sidecar claims
4. **Secret redaction** - Logs don't leak credentials
5. **Timeout enforcement** - Sidecar can't run indefinitely

## Current Limitations

### STUB Implementation

The `run_openhands_agent()` function in the sidecar is currently a STUB:

```python
def run_openhands_agent(...):
    return {
        'success': True,
        'changed_files': [],
        'error': None,
        'stub': True
    }
```

**To enable real OpenHands:**
1. Install `openhands-ai` package
2. Configure provider credentials (OpenRouter API key)
3. Implement actual agent execution
4. Capture real file changes

## Next Steps (Phase 2)

1. Create isolated worktree infrastructure
2. Implement real OpenHands agent execution
3. Add comprehensive path security tests
4. Build hermetic E2E test
5. Restore old Teacher sandbox functionality

## Files Changed

| File | Status | Purpose |
|------|--------|---------|
| `scripts/openhands_sidecar.py` | NEW | Sidecar protocol |
| `bossman/apprentice/openhands_client.py` | UPDATED | Real client |
| `docs/v6/PHASE1_IMPLEMENTATION.md` | NEW | This doc |

---

**Status:** Phase 1 complete, Phase 2 pending
