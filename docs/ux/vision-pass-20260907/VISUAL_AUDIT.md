# Visual audit — Command Center, vision pass 2026-09-07

Branch: `ux/vision-full-page-pass-20260907` (from `origin/v6/velocity-phase0-baseline-20260907`).
Method: real uvicorn server + real Chromium (`/opt/pw-browsers/chromium-1194`), login by token,
every route from `window.__bxPages` (36 routes, see `../VISUAL_PAGE_INVENTORY.md`), screenshots
read and inspected by eye, structural audit by JS (document overflow, off-screen controls, inner
overflow wider than `#view`, text < 10.5px). Presentation-only changes (CSS + class names); no
request, state, approval or navigation logic was touched.

Viewports: 1920×1080, 1440×900 (light and dark), 1366×768, 1024×768, 390×844 — each route, before and after.
Screenshots: `before/<route>_<width>_before.png`, `before/<route>_1440_full_before.png` (full page),
`before/<route>_1440_dark_before.png`; same names with `_after`. Open states: `before/<state>_1440_before.png`.
Files are downscaled 50 % and palette-quantised (Pillow) to keep the tree small.

## Severity legend
* **P0** — hidden/off-screen controls, unusable modal, primary navigation missing, page not fitting at 1920×1080, error looking healthy.
* **P1** — overlaps, unreadable/broken text, unstyled critical form, clipped content without affordance.
* **P2** — polish: consistency, spacing, duplicated titles.

## Cross-cutting findings (shell / design system)

| # | Sev | Finding (observed → expected) | Root cause | Fix (files) | Status |
|---|---|---|---|---|---|
| S1 | **P0** | Desktop dock (`#desktop-dock`, primary app navigation) missing on every page taller than the viewport: at 1024×768 on `#/skills` its box is at y = 8228 (measured); at 1440×900 on short pages it sat *inside* the shell covering the last panel («Последние события», Video Studio timeline, Web Designer templates). Expected: the dock never covers content and is always reachable. | `backdrop-filter` on `.shell` makes it the containing block for `position: fixed` descendants. A first attempt (glass on `.shell::before`, dock truly `fixed`) mirrored the defect: at Playwright's default 1280×720 the fixed dock (y 636–706) sat over the Web Designer preview iframe (y 602–853) and swallowed the picker click (`elementFromPoint(761, 654)` → dock). | `ui/desktop.css`: the dock is now the second row of the `.shell` grid (`grid-template-rows: minmax(0,1fr) auto`, `grid-column: 1/-1`), in normal flow under `#view` inside the frame; window scrolling and `window.scrollTo` on route change are unchanged. Verified: `elementFromPoint(761, 654)` at 1280×720 → `iframe.bd-frame` (before: dock); dock at y 1311–1381 = end of content. Regression test `tests/test_visual_smoke_ui.py::test_dock_never_covers_content_and_is_reachable`. | fixed; verified 1280×720, 1440×900, 1024×768 |
| S2 | P1 | Sidebar navigation clipped mid-item («Video Studio» half visible) with no scroll affordance at 900px and shorter; at 768px 6 items hidden. | `.nav` scrolls but nothing signals it. | `ui/style.css`: scroll-driven bottom fade (`scroll-timeline` / `animation-timeline`, graceful no-op elsewhere), denser nav items under 820px height. | fixed |
| S3 | P1 | Three radius scales (style 9/12/16, theme 10/14/20/26, desktop hard-coded 13/18/20/22) and two status palettes (light `--ok #12a05c` vs `--bx-mint #08794f`, dark `#33d17f` vs `#35d29a`) — same state, different green. | No shared tokens between layers. | `ui/style.css` defines `--radius-xs…2xl`, `--space-1…8`, `--shadow-lg/float`, accessible light status colours; `ui/theme.css` and `ui/desktop.css` alias them (`--bx-r-*`, `--bx-mint/amber/rose/azure`). Same rendered values ±2px, single source. | fixed |
| S4 | P1 | Native `<input type=file>` («Choose Files / No file chosen») rendered raw in the home-v3 composer and the chat page. | No shared component. | `ui/style.css`: `input[type=file]::file-selector-button` styled as a secondary button; `.bx-attach` row. | fixed |
| S5 | P2 | Topbar title duplicates the page H1 on 25 pages («Модели» twice); MVP pages use three different header patterns (H1 + subtitle, uppercase eyebrow, none). | Two page generations. | Not changed (DOM restructure) — recorded for V6 in `docs/v6/design/INPUT_FROM_VISION_SWEEP.md`. | deferred (V6) |
| S6 | P2 | `--font` (system) vs `--bx-font` (Inter-first, falls back to system) — two font stacks; identical on machines without Inter. | Two layers. | Not changed (no webfonts allowed; no visual difference here). | deferred (V6) |

## Per-route findings

Viewports tested for every route: 1920, 1440 (light+dark), 1366, 1024, 390. «—» = nothing beyond the cross-cutting items.

| Route | Findings | Files changed | Status |
|---|---|---|---|
| `#/home` | S1 (dock over «Последние события» at 1440×900), S2. After: dock sits in the frame under the last panel. | desktop.css, style.css | fixed |
| `#/models` | S1, S5. Wizard (step 1/2) renders inside the viewport, Esc closes. | — | ok |
| `#/agents` | S1, S5. New-agent modal fits at 1440×900. | — | ok |
| `#/tasks` | S1. FUNCTIONAL F1 (see below). | — | ok |
| `#/schedules` | S1, S5 (eyebrow-style header). | — | ok |
| `#/approvals` | S1, S5. Empty state honest («Пока ничего не ждёт решения»). | — | ok |
| `#/system` | S1. Stopped workers shown as «ОСТАНОВЛЕНО» grey, warnings amber — truthful. | — | ok |
| `#/settings` | S1. FUNCTIONAL F2. | — | ok |
| `#/video-studio` | **P1** studio height computed from the window (`calc(100vh - 132px)`, min 650) ignores the shell frame → ~110px taller than the space, timeline under the dock, page scrolls. | desktop.css (`.view .vs-studio { height: calc(100dvh - 250px); min-height: 640px }`) | fixed at 1440×900; at 768px height still scrolls ~120px (studio min-height) — deferred, needs studio layout work |
| `#/bossman-chat` | **P1** unstyled form: raw textarea (mono, 4 rows, 180px wide), raw select, buttons misaligned, «Убрать вложения» as a primary-size button with no attachments. | video_chat.js (classes only), theme.css `.bx-chat*`, style.css | fixed |
| `#/home-v3` | S4 (raw file input in the command bar). P2: the second «Главная» (`home-v3`) coexists with `home` in the sidebar; only `home` is listed under «Основное». | style.css | fixed (S4); duplication → V6 input |
| `#/apps` | P2: «Открыть» button on stopped apps rendered in a pale accent that reads as disabled at 390px (it is the state colour from the manifest). | — | deferred P2 (needs manifest-driven accent decision) |
| `#/overview` | S1 (dock over «Recent Activity»). FUNCTIONAL F3 (RAM 0.0 / 125.0 GB). | — | S1 fixed |
| `#/missions` | S1, S5. Modal fits. | — | ok |
| `#/router` | — | — | ok |
| `#/governor` | — | — | ok |
| `#/resources` | S1 (dock over empty state). FUNCTIONAL F3. | — | S1 fixed |
| `#/skills` | S1 (dock at y≈8228 → invisible). | desktop.css | fixed |
| `#/terminal` | P2: delete-folder icon button (trash, 12px glyph, no border) is easy to miss. | — | deferred P2 |
| `#/benchmarks` | S1. | — | fixed |
| `#/browser` | S1. Empty-state copy exposes internal state name «EMPTY». | — | P2 copy → V6 input |
| `#/coding` | S1. | — | fixed |
| `#/agentmap` | S1. | — | fixed |
| `#/orchestras` | — | — | ok |
| `#/forks` | S1. | — | fixed |
| `#/healing` | S1. | — | fixed |
| `#/openrouter` | P2: «Connect» is a full-width block button while every other page uses a right-aligned primary CTA. | — | deferred P2 (page-local style; token pass first) |
| `#/command` | **P1 (truth)** «ConnectError» under «Сессий OpenCode нет» rendered as a dim caption — an outage looked like «no data». Page also renders in a 560px column on desktop (it is the phone console). | mobile.css (`.cmd-empty > .xsmall` error chip) | fixed; column width → V6 input |
| `#/builder` | S1. | — | fixed |
| `#/images` | — (tabs are `<button>`s inside `.tabs`; clicking through Генерации/Шаблоны/Очередь not automated because labels are rendered as links in this build). | — | ok |
| `#/trading_lab` | **P1** step tiles at 1440: status pill («РЕАЛЬНО РАБОТАЕТ», nowrap) squeezed the title column to ~45px → «Приё / м / матер / иала», detail broken per word. | theme.css (`.bx-tile-head` wraps, title min 150px) | fixed (also fixes every other `tile()` user: agents, missions, apps) |
| `#/mission_console` | P2: `.mc-label` 10px uppercase labels (8 per page) are at the readability floor; 390px quick-command chips are a horizontal strip by design (`overflow-x: auto`). | — | deferred P2 (console2030 has its own type scale; V6 input) |
| `#/web_research` | P2: stat row «нет данных ×4» had no visible gap between columns. | theme.css (`.bx-stats` gap 24px) | fixed |
| `#/control` | — | — | ok |
| `#/web_designer` | S1 (dock over template grid). Editor: code / preview / inspector fit at 1440. | desktop.css | fixed |
| `#/objectives` | S1. Autonomy-off notice is explicit — truthful. | — | fixed |

Mobile 390×844: no catastrophic breakage on any route (drawer nav, bottom `mobilenav`, composer stacks). Inner horizontal scrollers on `mission_console` chips and `trading_lab` pills are intentional strips; the document itself never scrolls horizontally (asserted by the smoke suite at 1440 and 1024).

## Open states (before → after)
* Models wizard step 1/2, agents/missions/schedules/skills modals, command palette, thinking pane, stale banner: rendered inside the viewport, Esc closes, no P0/P1. Dark theme: same.
* Video Studio new-project → editor → Цвет/Звук/VFX/ИИ → Экспорт: dialogs fit; timeline no longer under the dock at 1440×900 after S1 + the studio height fix.
* Web Designer: project creation, editor with live preview and inspector — fine. Responsive preview is a `<select>`; pointer events do not reach the sandboxed iframe here (environment limitation).

## FUNCTIONAL_FINDINGS (not touched — handed off)

| # | Route / component | Observed | Expected | Likely owner |
|---|---|---|---|---|
| F1 | `#/tasks` composer, button «По расписанию…» | Click calls `start(false)`: creates the task with `run_now: false` and shows no schedule dialog; label promises scheduling. | Either open the schedule modal (`openScheduleModal`) prefilled with the prompt, or relabel «В очередь без запуска». | `ui/pages.js` TasksPage / scheduler contract |
| F2 | `#/settings` «Выйти» | Logs out immediately, no confirmation; the login screen then shows «Сессия завершена…» (correct). | Confirm dialog (logout ends the server session, per the help text). | `ui/pages.js` SettingsPage / auth |
| F3 | `#/overview` Compute, `#/resources` «Память сервера» | «0.0 / 125.0 ГБ» while `#/system` shows 2.1 / 15.7 GB on the same server. `bcc/features/resources.py:47` falls back to `128000` MB when no metrics row exists (workers not started). | Show «нет данных» (honest) instead of a fabricated total. | `bcc/features/resources.py` (metrics/resource policy) |
| F4 | `#/home-v3` vs `#/home` | Two landing pages both titled «Главная»; sidebar shows only one, palette shows both. | One landing page or distinct titles. | `ui/app.js` LANDING/SUPERSEDED |
| F5 | `#/apps` cards | «Открыть» rendered in manifest accent at ~40 % which reads as disabled on stopped apps. | Disabled state must be `disabled`, enabled state full contrast. | `ui/pages/appcards.js` + app manifests |
| F6 | `#/browser` empty state | Copy shows the internal state token «EMPTY». | Human copy. | `ui/pages/browser.js` |

## Performance (login + home load, `window.__bxPreload = false`, response bodies summed)

| | before | after | Δ |
|---|---|---|---|
| static JS bytes | 296 898 | 297 042 | +144 (class names in video_chat.js) |
| static CSS bytes | 103 853 | 110 712 | +6 859 (tokens, file-input component, chat layout, comments) |
| requests | 37 (37–40 across runs; API polling noise) | 37 / 40 / 40 across three runs | same static request list |

No new dependencies, webfonts or animation libraries.

## Automated coverage
`command-center/tests/test_visual_smoke_ui.py` — every route from `window.__bxPages` at 1440×900 and 1024×768:
`#view` rendered, no `pageerror`/console errors, `scrollWidth <= clientWidth + 1`, sidebar nav present, dock present and entirely below `#view` (never over content), no controls beyond the right edge; plus a negative control on the long Skills page: the dock is below the view before scrolling and fully inside the window, hit-testable, after scrolling to the end. Result: 3 passed; with `test_ux2_pages_sweep.py` and `test_v6_lazy_pages.py`: 8 passed.

`tests/test_web_designer_sandbox_ui.py::test_picker_still_works_and_frame_is_isolated` (the CI check that caught the fixed-dock overlay) fails in this container on the base branch as well — real pointer clicks do not reach the sandboxed iframe here (environment limit, recorded in the inventory). The geometric criterion it depends on was verified directly: `document.elementFromPoint` at the preview `h1` (761, 654 @1280×720) returns `iframe.bd-frame` after the fix and the dock before it.
