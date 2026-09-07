# Input for the V6 design refresh — from the vision sweep of 2026-09-07

This is *input*, not a redesign. It records what the full-page Chromium sweep
(36 routes × 5 viewports × light/dark + open states, see
`docs/ux/vision-pass-20260907/VISUAL_AUDIT.md`) showed about the current UI, so the V6
design work starts from measured facts rather than impressions.

## 1. What already works and should be kept
* The Bossman language holds together: deep blue/cyan/violet, translucent panels on a
  wallpaper, rounded 20–24px desktop shell, restrained pills. Dark mode is the stronger
  of the two; light mode is legible after the status-colour normalisation.
* Empty states are honest everywhere (`Пока ничего не ждёт решения`, `Канал молчит — это
  не сбой`, autonomy-off notice on Objectives). Keep this tone as a hard rule.
* The `tile()`, `panel()`, `pill()`, `stat()` helpers in `ui/pages/_ui.js` + `theme.css`
  are the de-facto component library for 28 of 36 pages. V6 should build on them, not on
  the MVP `.panel/.card` classes.

## 2. Structural facts V6 must design around
1. **Two page generations.** 8 MVP pages (`ui/pages.js`) use `.panel`, big H1 + subtitle
   or an uppercase eyebrow; 28 feature pages use `bx-*`. The topbar repeats the H1 on 25
   pages. Decide one header pattern (recommendation: topbar owns the title + actions,
   page body starts with content; remove page-level H1s).
2. **Two landing pages** (`home` and `home-v3`), both titled «Главная»; `command`
   (phone console) and `control` (owner console) are both titled «Пульт». Navigation
   needs one landing, one console, distinct titles.
3. **The desktop dock is app-level navigation** (6 apps). After the containing-block fix
   it is really fixed; V6 should decide whether the dock and the sidebar «Основное» group
   duplicate each other (today they do for Главная/Приложения/Video Studio/Веб-дизайн).
4. **Video Studio and the mission console bring their own type/colour scales**
   (`video_studio.css`: 9–12px text, own dark palette; `console2030.css`: 10px labels).
   Both sit below the 11px floor used elsewhere. V6 must either lift them to the shared
   scale or explicitly declare them «dense professional surfaces» with their own floor.
5. **Height budget on desktop:** shell frame 16px, topbar 62px, testing banner 44px (when
   recording), view padding 24px, dock band 96px → ~240px of chrome. Full-height tools
   (Video Studio, Web Designer editor, terminal output) must size from the shell, not from
   `100vh`. At 1366×768 only ~520px remain — V6 should define a compact desktop mode
   (smaller dock, collapsible testing banner).

## 3. Token decisions taken now (so V6 can extend, not re-invent)
* Radius scale in `style.css`: 6 / 10 / 12 / 14 / 16 / 20 / 24 (`--radius-xs…2xl`);
  `theme.css --bx-r-*` and `desktop.css` alias it.
* Spacing scale `--space-1…8` (4-px grid) in `style.css`; `theme.css --bx-1…9` is the same
  grid — V6 can collapse the two names.
* Status colours: one source (`--ok/--warn/--err/--info`), light values chosen for ≥ 4.5:1
  at 11px uppercase (`#0e8a52 / #946000 / #d0333b / #1f6fea`). `--bx-mint/amber/rose/azure`
  are aliases. Violet (`--bx-violet`) and ember stay brand-only, never status.
* Shadows: `--shadow-sm / --shadow / --shadow-lg / --shadow-float`. Glow (`--bx-glow`)
  exists but is used only on the primary command button — keep it that way.
* Shared components added: `input[type=file]` (file-selector button), `.bx-attach`
  attachment row, scroll fade on the sidebar nav, error chip for failure reasons in the
  phone console.

## 4. Findings that need design (not CSS) — open questions for V6
* Page header pattern (see 2.1) and whether the page subtitle («Что происходит с
  сервером прямо сейчас…») belongs in the topbar, a help popover, or nowhere.
* App cards: manifest-driven accents at 40 % read as disabled. Define enabled / disabled /
  stopped visuals independent of the app accent.
* Full-width block CTA («Connect» on OpenRouter, «Открыть миссии» on the phone console)
  vs right-aligned primary elsewhere — pick one rule per context (form vs. page).
* Terminal «allowed folders» list uses an icon-only trash button; define a list-row
  action pattern (visible label on hover or a consistent icon-button style).
* Mobile 390: horizontal chip strips (`mission_console`, `trading_lab` pills) are fine as
  a pattern but need a visible scroll cue.
* Copy: internal tokens leak into UI («Состояние: EMPTY», `queued:0 · leased:0`).

## 5. Measurements to carry forward
* Login + home: JS 297 042 B, CSS 110 712 B, 37–40 requests (API polling noise), no
  webfonts. Any V6 refresh should stay within +10 % CSS and add no webfont unless
  self-hosted and measured.
* Automated guard: `command-center/tests/test_visual_smoke_ui.py` (structure, not pixels).
  V6 should extend it with the new header pattern and dock rules rather than add pixel diffs.
