# Phase 2: Isolated Worktree Infrastructure

**Date:** 2026-09-08
**Status:** COMPLETE

## What Was Built

### 1. IsolatedWorktree Manager

**File:** `bossman/apprentice/isolated_worktree.py`

Manages disposable Git worktrees for OpenHands execution:

```python
with IsolatedWorktree('/path/to/repo') as worktree:
    evidence = worktree.derive_evidence()
# Automatically cleaned up
```

**Security properties:**
- OpenHands never edits owner's primary checkout
- Pre-existing changes don't contaminate evidence
- Worktree destroyed after task (unless `keep_after=True`)
- Records pre-run and post-run HEAD

### 2. Evidence Derivation

`derive_evidence()` returns:
- modified_files: files changed from HEAD
- added_files: new tracked files
- deleted_files: removed files
- untracked_files: new untracked files
- patches: diff content for each modified file
- head_before: SHA before task
- head_after: SHA after task

### 3. Hermetic Tests

**File:** `tests/apprentice/test_worktree_isolation.py`

Tests:
- Worktree creation
- Isolation from source repo
- Evidence derivation
- Cleanup
- keep_after flag

## Files Changed

| File | Status | Purpose |
|------|--------|---------|
| `bossman/apprentice/isolated_worktree.py` | NEW | Worktree manager |
| `tests/apprentice/test_worktree_isolation.py` | NEW | Hermetic tests |
| `docs/v6/PHASE2_WORKTREE.md` | NEW | This doc |

---

**Status:** Phase 2 complete
**Next:** Phase 3 - Full integration and adversarial tests
