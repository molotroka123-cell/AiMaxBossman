# Shared convergence lane

Canonical shared integration branch for the final Bossman closure:

`claude/bossman-final-completion-kymr05`

PR: #62

Rules for all coding agents/models during this convergence run:

1. Push production fixes to this branch only. Do not create parallel closure branches or duplicate PRs.
2. Fetch/re-read the remote branch head immediately before every write. Other agents may push concurrently.
3. Fast-forward only. Never force-push the shared branch.
4. Preserve other agents' compatible work; do not reset/rewrite branch history to an older local SHA.
5. Keep commits logically scoped and tested. Avoid commit spam and unrelated feature expansion.
6. Completion truth, effect-time authority, evidence integrity, privacy, optional-subsystem startup isolation, and exact-SHA evidence must not be weakened for green CI.
7. File Intelligence remains an external, local-first sidecar boundary. No vendored AGPL C++, no raw shell flag channel, and no reachable auto-apply shortcut.
8. When concurrent changes conflict semantically, keep the stronger safety/verification contract and repair forward on this branch.

This file is coordination metadata only; it is not a declaration that the current branch head is a frozen or release-proven SHA.
