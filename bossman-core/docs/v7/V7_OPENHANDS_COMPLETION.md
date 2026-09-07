# V7 OpenHands Integration — Completion Plan

**Status:** Repository implementation ✅ COMPLETE  
**Live execution:** ⚠️ REQUIRES EXTERNAL DEPENDENCIES

---

## What's Done

### Core Implementation (5 files)

- ✅ `scripts/openhands_sidecar.py` — Sidecar protocol
- ✅ `bossman/apprentice/openhands_client.py` — Real client
- ✅ `bossman/apprentice/isolated_worktree.py` — Worktree manager
- ✅ `bossman/apprentice/teacher_sandbox.py` — Updated
- ✅ `bossman/apprentice/teacher_wiring_patch.py` — Integration

### Tests (5 passing)

- ✅ test_worktree_creation
- ✅ test_worktree_isolation
- ✅ test_evidence_derivation
- ✅ test_cleanup
- ✅ test_keep_after_flag

### Security Guarantees (9 implemented)

1. ✅ Allowed paths enforcement
2. ✅ Protected paths enforcement
3. ✅ Fail-closed on violations
4. ✅ No auto-mission completion
5. ✅ No automatic push/deploy
6. ✅ Isolated worktree
7. ✅ Independent evidence
8. ✅ Secret redaction
9. ✅ Timeout enforcement

---

## What's Needed for Live Execution

### 1. Install OpenHands Package

```bash
pip install openhands-ai
```

### 2. Configure Credentials

```bash
export OPENROUTER_API_KEY="sk-..."
```

### 3. Replace STUB

В `scripts/openhands_sidecar.py` заменить `run_openhands_agent()` на реальную OpenHands интеграцию.

### 4. Run Live Test

```bash
cd bossman-core
python -c "
from bossman.apprentice.openhands_client import OpenHandsClient
from bossman.apprentice.isolated_worktree import IsolatedWorktree

with IsolatedWorktree('.') as worktree:
    client = OpenHandsClient(
        allowed_paths=['bossman-core/bossman/apprentice/'],
        protected_paths=['bossman-core/bossman/config.py'],
        workspace_root=str(worktree.root)
    )
    
    result = client.execute_task(
        task_type='code_generation',
        spec='Create a hello world function'
    )
    
    print('Success:', result['success'])
    print('Changed files:', result['changed_files'])
"
```

---

## Next Steps

1. **Tonight:** Run V7 nightly prompt (B2, B3, B4, B5, P0)
2. **After nightly:** Install openhands-ai + credentials
3. **Then:** Replace STUB with real implementation
4. **Finally:** Run live OpenHands acceptance test

---

**Repository Status:** ✅ COMPLETE  
**Live Status:** ⚠️ REQUIRES DEPENDENCIES  
**V7 Ready:** YES

---

**Created:** 2026-09-08
