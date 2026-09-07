# Phase 2: Git Worktree Utility

**Date:** 2026-09-08  
**Status:** IMPLEMENTED AS UTILITY — NOT THE OPENHANDS SECURITY BOUNDARY

`bossman/apprentice/isolated_worktree.py` creates disposable Git worktrees and can derive file-change evidence. It is useful for trusted/local engineering workflows and for keeping ordinary edits away from the primary checkout.

## Important security correction

A Git worktree is **not** a sufficient sandbox for an untrusted coding agent:

- it exposes the full repository tree rather than Bossman's sanitized ProblemBundle;
- linked worktrees share Git administrative state with the source repository;
- repository configuration/ref operations therefore have a larger blast radius than a standalone sanitized repository;
- it is filesystem isolation only in the narrow sense of a separate checkout, not OS/container isolation.

Therefore production OpenHands wiring MUST NOT pass the owner checkout or a linked `IsolatedWorktree` directly to the untrusted OpenHands process.

The authoritative OpenHands path is instead:

```text
ProblemBundle
  -> sanitized temporary standalone Git repository (no remote)
  -> OpenHands sidecar
  -> Bossman independently derives Git changes
  -> untrusted candidate patch
  -> existing PatchVerifier on verifier worktree
```

This is implemented by `OpenHandsTeacherClient` at code SHA `b3924b65c25686560b54265d66188659908a1b24`.

## What IsolatedWorktree is still good for

- trusted local test/check workflows;
- disposable developer checkouts;
- evidence experiments where the process is already trusted;
- future verifier-side staging when combined with the existing policy layer.

Do not describe this module alone as preventing an untrusted process from reaching source-repository Git metadata. For stronger runtime isolation of OpenHands itself, use an OpenHands Agent Server / container workspace while retaining Bossman's sanitized-bundle and PatchVerifier authority model.
