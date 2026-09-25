# Bossman final UX swarm audit — interim, not release acceptance

TESTED_SHA=`dd89151ec1c81881c0ac418d0970aa70f9186d2d`  
BRANCH=`integrate/bossman-1.7-unified-20260925`  
ASTER_CODE_WRITES=0

This is a read-only source and isolated-browser-test checkpoint. It is **not** a
manual owner-desktop, Telegram, installed-product, or seven-agent live UX PASS.
The Computer Use skill's `sky.list_apps()` failed after the prescribed retry
and reset with `Computer Use native pipe is unavailable ... os error 2`.
No owner desktop input or screenshot was possible. Parallel read-only reviews
covered install, controls, responsiveness, Studio, Telegram, page controls,
and hostile-UX scenarios. Agent thread limits prevented seven simultaneous
agents; the same seven surfaces were reviewed in batches.

Isolated Command Center Chromium tests at this SHA:
`test_ux2_pages_sweep.py`, `test_ux2_owner_actions.py`,
`test_release_ux_torture.py`, `test_pit_foundation.py`:
**58 passed, 1 teardown warning** (`websockets.exceptions.InvalidState` while
test Uvicorn was closing). These tests do not certify the installed owner app.

## Repro packets for GPT-6 builder

All packets below have `REPRO_RATE=not run`, `SCREENSHOT=unavailable`, and
`ACTUAL=source-predicted, not observed in the owner UI`. Severities are
provisional until a live repro; no issue is marked closed.

| ID | Provisional severity | Surface / action / expected / source-predicted actual | Code pointer |
| --- | --- | --- | --- |
| UX-001 | P1 privacy | Jeff: chat a private fact, send `/privacy personalization off`, then ask another question via free remote route. Reply promises only the current request; constructed remote messages still include stored chat history. Expected: promise matches transmitted context. | `command-center/bcc/pit/runtime.py:785,907,937` |
| UX-002 | P1 privacy | Jeff: `/pause_memory`, chat, restart, ask about the exchange. Reply says no new memory is recorded, but `store.remember` still inserts durable history. Expected: pause semantics and reply agree. | `command-center/bcc/pit/runtime.py:740,281,973` |
| UX-003 | P1 authority UX | Owner palette: start a coding job and put another task in `waiting_approval`, then choose “Остановить все активные”. UI filters only regular `running/queued/paused` tasks and can report success while other authority planes remain active. Expected: either global STOP or explicitly limited label/result. | `command-center/ui/pages.js:1289-1309`, `command-center/bcc/terminal_cli/cli.py:810` |
| UX-004 | P1 launch | Fresh source start without Python 3.11+, or restart offline; `start-bossman.ps1` exits with manual `winget` instruction or reinstalls packages on every default run. It does not invoke PIT/Jeff start. Expected: one-click owner launch and Jeff restart. | `start-bossman.ps1:59-60,74-90,137`, `command-center/bcc/pit/cli.py:83` |
| UX-005 | P1 installed version | Run build A on default port, unpack build B, launch B. Desktop identity check accepts same app name and can connect B's window to A's server. Expected: exact build/backend identity. | `command-center/bcc/desktop.py:158,1001` |
| UX-006 | P2 Studio | Select Qwen edit, attach a video as reference or >3 images, submit. Composer permits choices the model/provider later rejects. Expected: type/count validation before submission. | `command-center/ui/pages/_studio.js:57`, `tools/studio_models.json:443` |
| UX-007 | P2 responsiveness | Keep `/api/missions` pending and navigate to Missions. Shared fetch has no timeout; render awaits it and can leave a skeleton indefinitely. Expected: bounded error/retry feedback. | `command-center/ui/api.js:126`, `command-center/ui/pages/missions.js:40` |
| UX-008 | P2 controls | Home “Создать агента” navigates to the agent list instead of opening creation; another click is required. Expected: direct create action or accurate label. | `command-center/ui/pages/home.js:445`, `command-center/ui/pages.js:804` |
| UX-009 | P2 duplicate work | Double-click Images “Запустить” before refresh. No pending guard is visible; each request can enqueue a separate image job. Expected: one intentional submission or explicit duplicate confirmation. | `command-center/ui/pages/images.js:190,434`, `command-center/bcc/features/images.py:492` |
| UX-010 | P2 duplicate work | Double-click Music “Сгенерировать трек”. Each click can start a provider task while the UI retains only the latest task ID. Expected: one task or visibility/cancellation of all tasks. | `command-center/ui/pages/music_studio.js:59,73`, `command-center/bcc/features/music_studio.py:86` |

Other unverified neighbours: OpenHands card lacks visible cancel despite a backend
cancel endpoint; Studio partial artifacts are not labelled; PIT malformed-update
offset may stall; owner approval page omits active leases. These need focused
reproduction before severity or release decisions.

## Gate snapshot

SCREENS=0 owner screens; BUTTONS_TESTED=0 live; BUTTON_PASS=unknown;
BUTTON_FAIL=unknown; DEAD_BUTTONS=unknown; P0=unknown; P1=unknown confirmed
(5 provisional candidates); P2=unknown confirmed (5 provisional candidates);
P3=unknown;
P50_UI_MS=unknown; P95_UI_MS=unknown. STARTUP, RESTART, STOP,
TELEGRAM_OWNER, JEFF, STUDIO, COMPUTER_USE=BLOCKED/UNVERIFIED.

Next required evidence: restore the native Computer Use helper, inspect one
returned Bossman window, run fresh-action/readback/screenshot UX cases on a
pinned SHA, then re-test builder fixes and neighbours. No final freeze.
