# Goal: live self-improvement capability tree

Branch: `goal/bossman-self-improvement-tree-20261005`.

## Purpose

When the existing bounded Evolution Loop becomes active, Command Center opens
`#/capability-tree` once for that campaign. The page shows the real campaign
status, current cycle, selected task and phase. It never starts a second learner
and never promotes a candidate into stable.

The owner can attach a durable note to an existing tree node. Notes are stored in
the configured Bossman data directory under
`evolution/capability-tree/owner-notes.json`; they do not overwrite evidence
status. The background feature mirrors changed runtime activity into
`activity-latest.json`.

## Deterministic discovery

`tools/bossman_capability_scan.py` walks every fetched `origin/*` tree and parses
the current checkout for argparse commands, API-like routes, ToolSpec entries,
plugin capabilities and UI pages. It calls no model. Presence of source is only
discovery evidence and can never become PASS.

Standalone:

```bash
python tools/bossman_capability_scan.py --repo . \
  --out capability-scan.json
```

From the authenticated Command Center page, **Проверить репозиторий без ИИ**
uses the same scanner after the normal code-root check. The second and later
runs show additions relative to the previous saved scan.

## API

- `GET /api/capability-tree` — seed tree + live evolution overlay + notes + latest scan.
- `POST /api/capability-tree/note` — note for an existing node only.
- `POST /api/capability-tree/scan` — deterministic scan of an allowed Git checkout.

## Evidence boundary

The bundled tree is reference-only. `code`, `recorded`, `reported`, `blocked`
and other source statuses are preserved. Owner notes, scanner results and model
claims cannot turn a node green. Promotion still requires exact-SHA tests,
independent verification, regression, restart/transfer evidence and the normal
review boundary.

Current North Star level is not advanced by this feature. It adds visibility and
durable bookkeeping around `SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT`; it is not
`SELF_REPAIR_SINGLE_CYCLE_PASS` by itself.
