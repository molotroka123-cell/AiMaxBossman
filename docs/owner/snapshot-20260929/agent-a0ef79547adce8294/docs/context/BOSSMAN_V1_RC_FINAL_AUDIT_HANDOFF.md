# BOSSMAN V1 RC — final audit handoff for GLM 5.3

Base reviewed: `c96bf5ca1117741f3ba6562003a3468c2d2985a1` (remote target at start).

## Integrated in this branch

- Provider-aware OpenRouter/Claude Opus 5 prompt caching in the existing Stage3 Gateway, including stable session affinity, 5m/1h TTL, fail-open, streaming telemetry, provider-cost-first Cost Governor accounting, dashboard card, tests, and concise architecture docs.
- Lane-2 LSP workspace confinement using the existing canonical `tools_code.resolve_root`; no duplicated root engine. Evidence: allowed/outside/traversal `PASS`, symlink `SKIP_HOST` on Windows.
- Lane-3 is evidence-only; no audit branch was merged.

## Remaining confirmed Lane-4 P1 work

Do not cherry-pick `699c7d6`; it is documentation-only.

### P1-1 Session -> Diff -> Merge

Reuse `/api/opencode/sessions/{id}/diff` and `CodingWorktreeManager.diff/merge`; do not add a diff engine. Current `CodingWorktreeManager.merge()` is unsafe/fake: it merges in a detached temporary worktree, removes it, and never advances the target branch. `test_polish_lsp_and_coding.py` masks this with `or True`.

Required minimal fix:

1. Link task drawer to its latest `opencode_sessions` row and existing diff endpoint.
2. For manager-backed sessions include authoritative `CodingWorktreeManager.diff` output; retain existing OpenCode/DB fallbacks.
3. Derive target branch server-side from the source repo; never trust a request ref.
4. Require source and session worktrees clean/committed. Under the existing merge lock, merge in the checked-out source repo; abort on conflict and return explicit blocked/dirty states.
5. Strengthen tests to prove target HEAD and files changed; remove `or True`.

### P1-2 Fake-green

Confirmed sites: `command-center/bcc/api.py` `/api/system`, `static/js/pages.js`, `static/js/app.js`, `static/js/pages/home.js`, `static/js/pages/overview.js`. Centralize mapping with precedence `OFFLINE > DEGRADED > UNKNOWN > READY`. Missing, empty, unrecognized, or HTTP-200-only states must never become READY.

### P1-3 Browser state

Use `BrowserManager.available` as authority. Add a small authenticated browser-health API (and system registry entry), fetch it with sessions, render explicit OFFLINE/UNKNOWN, and disable New Window unless READY. `[]` means “no sessions”, never “browser healthy”.

## Required GLM 5.3 verification

- Prompt Cache/Gateway/Cost Governor/cloud policy/streaming tests.
- `test_v21_opencode.py`, strengthened `test_polish_lsp_and_coding.py`, `test_api.py`, `test_feat_browser.py`.
- Lane-3 invariants: boot, Gateway barriers, FactStore, approval replay, Cost Governor, Pythia fail-soft, Stage13, restart durability.
- `node --check` on every touched JS; `python tools/ci_secret_scan.py`; `git diff --check`; full available regressions.

Residual security P2 (do not widen this RC patch unless necessary): LSP workspace is confined, but the requested document `uri` is not yet independently proven inside that workspace.
