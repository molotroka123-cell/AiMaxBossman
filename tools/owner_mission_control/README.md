# Bossman OS Owner Mission Control — prototype (16 Oct 2026)

Three deliberately separated parts:
1. **Interactive UI** (`index.html`): Overview, capability tree, agents/tasks, model arena, GitHub/CI and token/budget tabs. Demo scenario is browser-only and cannot dispatch tasks.
2. **Evidence layer**: fixed GitHub snapshot linked to PRs #108–112, all marked as `snapshot`. PR #112 was OPEN at check; CI could **not** be established by the combined statuses endpoint. 922 leaves refer to PR #112's claim, not independently validated statuses.
3. **Read-only local telemetry** (`owner_bridge.py`): loopback HTTP, optional `psutil` physical CPU and RAM. GPU, agents, token usage, spend, actual model tests and autonomy = null until real collectors are integrated.

## Launch on the owner's PC

```bash
python -m pip install psutil
python owner_bridge.py --port 8765
```

Open http://127.0.0.1:8765 and switch to **Real / read-only**. The page does NOT auto-reload and does NOT continuously poll yet; change mode to reload local data. Demo changes are never published, and no API key is required.

## Integrate with AiMaxBossman

- Map the **real registry** from `tools/tree_registry_sync.py registry` to versioned `GET /api/owner/tree` with leaf status + evidence SHA + timestamp; do not transform presence into VERIFIED.
- Add **local collectors** for Windows CPU/RAM/GPU, agent lifecycle, queue, model trials, provider token billing, and evolution gates. Every metric requires `source`, `captured_at`, `scope`, `sample_type`; unavailable fields are null.
- Use authenticated owner-only endpoints, strictly read-only initially. Add owner approval, STOP and budgets as a separate reviewed phase; NEVER auto-merge PRs or auto-trigger paid providers.
- Add provider-specific usage ledger and distinguish price estimate from billed costs. Limit policy enforcement must happen on backend, not slider UI.
- Add GitHub Actions workflow/check-runs adapter for exact SHA; combined statuses alone are insufficient for CI.
- Build an installed Windows owner test with screenshots, error budget, evidence and regression coverage before shipping.

## Known limits

This is a proof of UI interaction, **not** the production Bossman Control Center. It does not prove live GPU readings, CI passing, local inference, self-improvement, spend or model quality. The GitHub snapshot is historical and requires refresh.
