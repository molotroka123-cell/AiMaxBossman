# Sandbox Dashboard acceptance session — V6 line (`c4253266`), session `7cc925717624`

**What this is:** a scripted, repository-level acceptance run of the real Command Center
(uvicorn, real Chromium 141 headless, testing-period recorder ON, background workers ON,
a fake model adapter that answers instantly). Five sequential tasks in one live session,
the shell kept open throughout, then compared with the owner's real session
`6cbb17ce84db`. **It is not owner-machine evidence:** no Windows, no local model, no real
provider, no FFmpeg encoder (libx264/aac absent in this sandbox).

Script: `scratchpad/accept_session.py` (kept outside the repo; reproducible from the
commands in it). Raw report: not committed (raw JSONL logs are excluded from the tree
since the 2 MB secret-scan limit; only this summary is).

## Sequence and outcomes

| # | Task | Outcome |
|---|---|---|
| T1 | research/file task via `/api/tasks`, Home + Missions pages open | `completed` |
| T2 | code task, Coding + Terminal pages open | `completed` |
| T3 | Browser page → «Новое окно» → address bar appears | opened in 0.8 s |
| T4 | two concurrent tasks (two agents), Orchestras + Agent map pages | both `completed` |
| T5a | Web Designer: project → code → AI edit (fake model) → then provider down | edit **200 and persisted**; provider down → **502 «модель недоступна: провайдер недоступен»**, code **unchanged** (no silent success) |
| T5b | Video Studio: create project → reload → thumbnail of a missing media → export with a wrong body | create 200; reload 200 (persistence); thumbnail **422** with an explicit message (not 500, not a fake 200); export refused 422 by request validation (`expected_revision` required) — a real export needs media + an encoder this sandbox lacks: **NOT_RUN** |
| — | 15 page navigations incl. Chat/history, Apps, Trading Lab, OpenRouter, 20 s idle with the WebSocket open, then two more navigations | all rendered; WS state stayed «live-обновления» |

## Telemetry (corrected detector, `#view` + `#modal-root` + `#toast-root`)

| Metric | Owner session `6cbb17ce84db` (5 777 records) | This run (294 records) |
|---|---|---|
| `ui.dead_click` | 37 raw (26 proven detector artefacts → 11 real candidates) | **0** |
| `ui.refused` | 71 | **0** |
| `http.error` (4xx/5xx recorded server-side) | 31 | 3 — all deliberate probes (two 422 by design, one 502 = the provider-down negative control) |
| HTTP ≥ 500 | 20 | 1 (the deliberate 502) |
| JS `pageerror` / console errors | present (see owner log) | **0 / 0** |
| tasks reaching a terminal state | stuck/blocked missions reported | 4/4 `completed` |
| `Services.start` | — | 187.13 ms |
| host RAM used (system-wide, includes Chromium) | — | 1290.4 → 1484.9 MB over 29.9 s (not a process RSS figure; no leak claim) |

## What this closes and what it does not

Closed at repository level on this HEAD: reconnect/refusal recording, mission terminal outcome,
Apps restart recovery (unit-tested; not exercised here because starting real apps spawns the
owner's applications), Web Designer AI-edit success and honest provider failure, Video Studio
project persistence and honest 4xx on missing derivatives, Trading Lab routes (200 on all
four), dashboard load path.

Still external: real Video Studio thumbnail/waveform/export with a real encoder; real
OpenRouter/local-model provider path and its on-screen error text; Windows desktop
behaviour; long-session (hours) stability; GPU/local-model figures.
