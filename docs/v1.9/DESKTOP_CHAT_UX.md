# Desktop chat (Bossman 1.9): `/chat.html`

A new desktop chat surface for the SAME Bossman. It is served by the existing
Command Center at `/chat.html` and uses the same backend, tasks, agents, models,
memory, approvals, STOP and budgets as the classic shell, the terminal
(`bossman chat`) and Telegram. A chat thread IS a terminal chat session
(`<data_dir>/terminal/sessions/<id>.json`): a conversation started in CMD is
visible in the web chat and vice versa. There is no separate data directory,
model fleet, memory database or task engine.

Status: implemented in the cloud on `claude/bossman-1.9-owner-bugtest-20260930`.
**NOT_RUN**: no test, browser session or owner scenario was executed in the
cloud (the owner forbade test runs there). CI runs the tests after the push;
the owner's bug test tomorrow on the Windows PC is the first real use.

This is a control surface, not a new level on the North Star ladder. It does
not change `SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT → … → REVENUE_CAPABLE_PILOT`
progress by itself.

## 1. How to open

| Way | What happens |
|---|---|
| `bcc-desktop --chat` | The desktop window opens `http://127.0.0.1:<port>/chat.html` instead of the Command Center root. Server checks, the lock file and the attach-to-running-server logic are unchanged. **Only while the BOSSMAN window is closed:** the chat window uses the same browser profile (`<data_dir>/desktop-profile`) and the same `desktop.lock`, so while the normal BOSSMAN window (shortcut, `start-bossman.ps1`, `python -m bcc.desktop`) runs, `bcc-desktop --chat` opens no second window. It exits with code 0 and prints the chat address (`http://127.0.0.1:<port>/chat.html`) and the way to it in the open window (Command Center → «Чат» → «Открыть чат»). `--print-launcher --chat` shows a shortcut command with `--chat` (to make an own chat shortcut by hand); without the flag nothing changes. `--install-shortcut --chat` and `--uninstall-shortcut --chat` refuse with exit code 2 and a Russian explanation: there is one BOSSMAN shortcut (same name and path), and the chat never replaces or removes it. |
| `http://127.0.0.1:8800/chat.html` | Any browser on this PC (default port 8800). |
| Command Center → **Чат** (sidebar, section «Главное») | Recent threads as links (`/chat.html#t=<id>`) and the link «Открыть чат». |
| `/chat.html#t=<id>` | Opens that thread directly; F5 on a running turn re-attaches to the live stream (replay from `after=0`). |

Login: the same token as Command Center. If the session is missing or expired,
the chat shows its own login card (token field is `type="text"` with
`autocomplete="one-time-code"` and a masked font, never a password field) and
continues where it stopped: a pending message is sent after login, a running
turn re-attaches.

## 2. What each control calls

| Control | Endpoint(s) | Notes |
|---|---|---|
| Thread list, search «Поиск чатов…» | `GET /api/chat/threads?q=&project=&archived=0\|1&limit=200` | Search is server-side over titles and turn texts (debounced 250 ms). Groups: Закреплённые / Сегодня / Вчера / На этой неделе / Ранее (local calendar). |
| «Новый чат» (Ctrl+N) | nothing until the first send, then `POST /api/chat/threads` | No empty threads are created. |
| Projects, Архив | `GET /api/chat/projects`; filter via `project=` / `archived=1` | A project is a label on a thread. |
| Send (Enter) | `POST /api/chat/threads/{id}/send {text, agent_id\|null, attachments, client_request_id}` | Same preflight → create → run as `POST /api/tasks`. A refusal (409) is shown as an error card; nothing is created. A network error retries with the SAME `client_request_id` (no duplicate task). If you open another chat while the send is in flight, the turn still goes to its own thread on the server, but the window you are in is not touched (no stream, no STOP, no address change); a short note says where the message went. |
| Live answer | `GET /api/events/stream?task_id=<tid>&after=<seq>` (SSE via `fetch`) | Dedupe by `seq`; reconnect with `after=<last seq>`, backoff 0.5 → 8 s with jitter; idle watchdog 45 s (server keepalive every 15 s); `stream.lagged` → reconnect from its cursor. Rendering is a frozen prefix: finished blocks are appended once and only the unfinished tail is re-rendered, so a selection or a code «Копировать» button in finished blocks survives the stream. While the answer streams, a half-typed `[label](https://exa` shows just the label and a URL still being typed stays plain text (no live link to a truncated domain); a finished `[label](https://…)` at the very end stays a live link and never shows a stray backslash (`https\://`); `**`, `*`, `_`, `~~` are never auto-closed. Final text and history are rendered as is. After `run.answer_reset` a retried step (fresh stream, attempt 0, idx 0) streams again. |
| Connection lost | the same stream | A banner above the input: «Связь с сервером потеряна — задача продолжается на сервере.», a countdown «Повтор через N с» (the real `delay_ms`), and the button «Сейчас» (reconnects at once, never a second connection). The network coming back (`online`) or returning to the window also reconnects at once. A transport error never marks the turn failed; only the server truth does. The banner hides when the stream opens. |
| «К последнему сообщению (End)» | — | The feed sticks to the bottom only while you are there: wheel up, PageUp / ArrowUp / Home (outside the input) or dragging the scrollbar up un-sticks it; coming within 4 px of the bottom re-sticks. While un-stuck, the round ↓ button above the input is shown (a dot when new text arrived); clicking it keeps the focus in the input. Smooth scroll unless the system asks for reduced motion. Replaces the old 120 px rule that pulled a reader back every frame. |
| Final truth | `GET /api/chat/threads/{id}` (fallback `GET /api/tasks/{id}`) | After `task.completed/failed/stopped/blocked`. |
| Steps of a finished turn | `GET /api/tasks/{id}/events?after=0&limit=2000` | Rebuilds tool cards and the panel for history turns. |
| STOP (red square, Esc) | `POST /api/chat/threads/{id}/stop` | Same engine stop as `POST /api/tasks/{tid}/stop` (leases revoked, pending approvals rejected). If the turn already finished, the server's final state is shown. Esc sends STOP only from the message input or from outside input fields; Esc in the title rename or in the chat search only cancels there. |
| Approve / Deny on an inline approval card | `POST /api/approvals/{id} {approve, by:'owner'}` | The existing approvals system; the card follows `approval.decided`. |
| «Отправить ещё раз» on an error card | the send above | Never labelled «Повторить». |
| «+» attachments, drag & drop, paste | `POST /api/chat/attachments?filename=&thread_id=` (raw body) | Removable chips with size; limit from `/api/chat/options` (20 MiB, 8 per message). An attachment belongs to the thread open when it was uploaded, so switching chats removes the chips with the note «Вложения убраны: они были загружены для другого чата. Прикрепите файлы снова.» (instead of a 409 on send). |
| Model picker | `GET /api/chat/options` + `GET /api/models/picker` | «Auto · Local-first» first; then agents grouped Локальные / Облако (бесплатно) / Облако (платно, с лимитом) / Недоступно, with alias and LOCAL / LAN / CLOUD and billing badges. Unusable entries are disabled with the refusal reason. The choice is an agent (the server has no per-message model override). |
| Claude / Codex subscriptions | same options (`subscriptions`) | Shown with their real login state and the note that they run ONLY through Agentic Rave; choosing one switches the composer to Agentic Rave mode. |
| Context meter «used / window» | `run.usage` | Used = `step_tokens_in` of the latest usage event (the prompt the model actually read), else `tokens_in`; window = `context_window` from usage, else the selected model's. «—» before any run. |
| Mic | `MediaRecorder` → 16 kHz mono PCM16 WAV in the browser → `POST /api/oss/speech/transcribe?language=auto` | Local faster-whisper. Disabled with the real reason when `/api/chat/options` → `speech.status` is not `configured` or the browser cannot record. Recording shows a timer and a cancel button; the text is put into the input for review, not sent automatically. |
| Chip «Thinking» (Ctrl+.) | toggles the right panel | State kept in `localStorage['bcc.chat.panel']`. |
| Chip «Источники» | opens the panel at Источники | Count = memory sources + URLs from tool results + attachments. |
| Chip «Память» | `GET /api/memory/config`, `GET /api/memory/stats` | Honest when no vault is configured; shows whether recall at turn start is on (`BCC_MEMORY_RECALL`). |
| Chip «Инструменты» | `GET /api/agents` (selected agent's `tools`) + `GET /api/capabilities` | For Auto: the tool set depends on the agent chosen at send time. |
| Chip «Agentic Rave» | `GET /api/rave/connectors`, `POST /api/rave {prompt, agents}`, `GET /api/rave/{rid}`, `GET /api/rave/{rid}/events?after=`, `POST /api/rave/{rid}/stop` | Agents from the connectors state (`local:local`, `claude:c`, `codex:x` only when logged in). Subscription agents still need the owner opt-in approval. Link to `/#/rave`. A card is live (polled every 2 s, STOP shown, sphere animated) only while the rave is `running`/`paused` or an agent is still live/queued/running/pausing/stopping; the server's `partial` (an agent failed, is blocked or waits for approval) ends it like `done`/`stopped`, labelled «частично — см. агентов». |
| Header title (click the pencil) | `PATCH /api/chat/threads/{id} {title}` | Enter saves, Esc cancels (and never sends STOP). |
| Code block «Копировать» | client clipboard | The button itself turns into «Скопировано ✓» for 1.5 s (its accessible name becomes «Код скопирован»); a toast appears only if the clipboard is unavailable. The code header with the button stays visible (sticky) while a long block scrolls. |
| Star (pin) | `PATCH … {pinned}` | |
| ⋯ → Переместить в проект / Архивировать / Открыть задачу | `PATCH … {project}` / `PATCH … {archived}` / `/#/tasks?task=<id>` | |
| Export | client-side Markdown download from the loaded thread | Titles, owner messages, answers, errors, model, status and task id. |
| Badges | LOCAL/LAN/CLOUD from the selected route; «Jeff …» only when `GET /api/jeff-settings/status` returns a heartbeat; «Только этот ПК» when the page is opened on a loopback address | Nothing is shown when the source is silent. |
| Sidebar bottom card | the selected route | «Локальный режим · Все данные на этом устройстве» ONLY for a local (on-device) model; LAN, local proxy and cloud get their own honest wording; Auto shows the last real route. |
| Settings / Command Center / help / theme | `/#/settings`, `/`, a shortcuts dialog, `localStorage['bcc.chat.theme']` (dark default) | |

Right panel «Thinking & Actions — Планирование, действия и источники»:
Кратко (agent, model, admission reason from send, router reasons from
`GET /api/router/explain?task_id=` when the task has a run, fallbacks), План
(a mission/workflow plan only if the task has one; otherwise «Для простого
запроса план не строится» plus the executed steps from `task.progress`),
Действия (tool calls with CMD / Browser / GitHub / … chips, redacted argument
summary, duration, ok/failed), Проверка (`evaluation.completed`, approvals),
Источники (`memory.recalled` sources from `GET /api/runs/{id}/events`, URLs from
tool results, attachments). **Hidden model reasoning is never shown:**
`run.reasoning_delta` is dropped by the client (only a counter is kept) and
nothing renders `<think>` content.

Answer footer: model, LOCAL / LAN / CLOUD, tokens, cost, duration. The place of
work is inferred from the model the run actually used: `run.usage` carries the
model alias, and when it differs from the one planned at send, the chat looks
that alias up in the picker data. The server sends no locality in `run.usage`
today; the client reads a `locality` field there only as forward
compatibility, and that branch does not fire now. This inferred place wins over
the guess made before sending; an unknown model gets no badge instead of a
wrong LOCAL. «Кратко» in the right panel, the header badge and the sidebar
Auto card («Последний ответ») use the same model and place as the footer. The
duration is taken from the server timestamps (`ts`) of `task.started` and the
terminal event, so a reopened thread or an F5 replay does not show «0 мс». A
step answer the server clipped at 16 000 characters («…[обрезано: N симв.]») is
replaced by the full task result instead of being shown twice.

Footer status bar: Streaming / Готово / Остановлено / Ошибка /
Переподключение…, tok/s (only the server-measured `gen_tps`, else «—»),
latency (server `ttft_ms`, else the window's own measurement «до 1-го токена»,
which includes queueing), cost («$0.00 local» for a local route, the real
`cost_usd`, or «цена неизвестна» when pricing is unknown), and a small waveform
of the actual characters received per 200 ms while streaming (hidden under
`prefers-reduced-motion`).

## 3. Keyboard

| Keys | Action |
|---|---|
| Enter / Shift+Enter | send / new line |
| Ctrl+N (⌘N) | new chat |
| Ctrl+K (⌘K) | focus chat search |
| Ctrl+. (⌘.) | toggle Thinking & Actions |
| / | focus the message input |
| End / Ctrl+End (outside input fields) | jump to the latest message and stick to the bottom again |
| Esc | close a menu or dialog; otherwise STOP the running turn (from the message input or outside input fields only) |
| ↑ / ↓ in menus | move between options |

## 4. Accessibility and look

Dark by default, light variant; system fonts only, no external assets. Every
icon button has `aria-label` and `title`; focus rings are visible;
`prefers-reduced-motion` stops every animation (static Sphere, no waveform, no
smooth scroll, no colour transition on «Скопировано ✓»); `prefers-contrast: more`
strengthens borders and muted text; `forced-colors` uses system colors.
Comfortable from ~1100 px; below 1100 px the panel slides over the chat; below
760 px the sidebar becomes an icon rail. No horizontal page scroll at any width.

Screen readers: the streaming answer row carries `aria-busy="true"` until the
turn ends, except while the turn waits for the owner's approval: then it is
`aria-busy="false"`, so the approval card is not held back until the turn ends.
The row stays fully rendered while it is live (`data-live="1"`). Its status line
(«Работаю… · шаг 2 из 8») is rebuilt only when the word or the step changes and
is not a second live region. One visually hidden
`<div id="chat-announce" role="status">` says once, on the live → final
transition, «Ответ готов», «Остановлено» or «Ошибка: <причина>». The final
state is announced again after «Отправить ещё раз» on the same turn. It also
says «Нужно ваше решение: <вид>» once per pending approval. That announcement
waits 0.4 s, so an F5 replay that brings the approval and its decision together
does not announce an approval that is already decided. Nothing is announced for
a turn the owner has navigated away from. Error cards (`role=alert`) and the
answer footer are not re-created on every repaint.

The skip link «Перейти к полю ввода» only moves the focus; it does not change
the address. A hash that is not `#t=<id>`, such as a hand-typed anchor, never
closes the open thread; the address is set back to the current thread. After
«Сейчас» in the reconnect banner hides the banner, the focus goes to the
message input. Esc that cancels an IME composition is not STOP. Clicking the
thread that is already open while its message is still being sent does not
reload it.

Long threads: finished answer bodies use `content-visibility: auto` (a live
answer or a row with focus is always rendered), so Ctrl+F, selection and the
accessibility tree keep working. The effect was measured only by a scout in a
cloud Chromium (1 000 turns: 1 015 → 47 ms first render); it is
INSUFFICIENT_EVIDENCE on the owner's PC until re-measured there.

## 4a. Page security (CSP + Trusted Types)

`chat.html` carries its own policy (a `<meta http-equiv>`, first in `<head>`):

```
default-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data: blob:;
media-src 'self' blob:; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none';
form-action 'self'; require-trusted-types-for 'script'; trusted-types 'none'
```

- No inline script: the pre-paint theme switch moved to `chat/theme-boot.js`
  (a classic `<script src>` in `<head>`, so the light theme still does not flash
  dark). No `on…=` handlers in the markup.
- Trusted Types with no policy: any HTML string written into the DOM
  (`innerHTML`, `insertAdjacentHTML`, `document.write`, `eval`) throws. The chat
  never does this (markdown is built with `createElement` + `textContent`), and a
  static test keeps it that way for `ui/chat/*.js` and `ui/api.js`.
- `connect-src 'self'`: all chat requests and the SSE stream are same-origin
  `fetch`. The chat does not use `api.js`'s WebSocket `EventStream`; if it ever
  does, Chromium treats same-origin `ws://` as `'self'`, but that is
  INSUFFICIENT_EVIDENCE until checked in the owner's browser.
- `style-src 'unsafe-inline'` stays because the login field carries the pinned
  inline `style="-webkit-text-security: disc"`; styles set from script (CSSOM)
  are not affected by CSP.
- `frame-ancestors` cannot be set from a `<meta>`; that is a known limit.
- Browser enforcement is INSUFFICIENT_EVIDENCE until the owner's Chrome/Edge
  shows 0 CSP violations in DevTools → Console for login, send, stream, STOP,
  mic and export (checklist item 15).

## 4b. Open-source decision: no dependency admitted

A strict judge scored 30+ candidates (markdown/streaming renderers, sanitizers,
DOM morphing, icons, popover/focus helpers, highlighters) and admitted none;
none scored 10/10. The two best, highlight.js 11.12.0 and remend 1.3.1 (8/10),
passed every hygiene check (tarball sha512 = registry integrity, permissive
licence, single ESM file, 0 secret-scan findings, 0 npm advisories with a working
positive control) but failed usefulness: highlighting is new scope and would add
the chat's first `innerHTML` sink, contradicting the Trusted Types policy above;
remend is tuned for GFM, not this parser (it bolds `2**10 = 1024` mid-stream and
appends a literal `~~`). DOMPurify, marked, markdown-it, morphdom, lucide,
floating-ui, focus-trap and the rest duplicate native or in-house capability or
break the single-file, offline or safety constraints. Instead, eight in-house
patterns were implemented, each tied to a verified defect: frozen-prefix
streaming render, `healTail`, `aria-busy` + one announcement, stick-to-bottom +
jump button, CSP + Trusted Types, `content-visibility`, the reconnect banner
with «Сейчас», and in-place «Скопировано ✓». These are UX and safety fixes;
they do not move the North Star ladder.

## 5. Known limits (honest)

- A message can pick an **agent**, not an arbitrary model: the server has no
  per-message model override.
- Only `openai_compat` providers stream tokens; Anthropic-API answers arrive
  whole (`run.assistant_message` with `streamed:false`).
- Rave cards live in the chat window for this session only; the rave itself is
  kept by the server and is always visible at `/#/rave`.
- Plans exist only for mission/workflow tasks; a plain chat turn shows the
  executed steps instead.
- tok/s and TTFT from the server appear only when the provider layer reports
  them (`gen_tps`, `ttft_ms` in `run.usage`); otherwise «—» or the window's own
  labelled TTFT.
- The send response carries `model.locality_detail` (local / lan / local_proxy /
  cloud); the chat prefers it, then the picker data, then the coarse
  local/cloud. After the run starts, the measured place of work wins (see the
  answer footer above).
- Only a live-typed link or URL at the very end of a streaming answer is
  neutralised; other half-written markdown (an unclosed `**`) may show its
  markers until the text is complete — by design, to never alter `2**10`.
- Memory sources of a turn come from the run log; if the run log is not
  readable, the section says that the turn did not rely on sources.
- In an ordinary browser tab the browser may keep Ctrl+N (new window) for
  itself; the «Новый чат» button always works. In the `bcc-desktop --chat`
  window the page receives the shortcut.

## 5a. Tests written for this surface (NOT_RUN in the cloud)

- `command-center/ui/tests/chat_core.test.mjs` (node:test): SSE parsing across
  split chunks and CRLF, reconnect with `after=<last seq>` without repeats, 401
  handling, seq dedupe, `answer_reset`, idx ordering, dropped
  `run.reasoning_delta`, tool cards and URL sources, terminal statuses,
  approvals, markdown escaping (`<script>` stays text, only http(s)/mailto
  links), date grouping, usage/cost/latency labels with null fields, picker
  grouping by billing, route-card wording, export, WAV encoding. Added for
  this round: a property test that frozen blocks + tail render exactly like the
  whole text at every chunk of 80 random chunkings of 8 adversarial documents
  (nested and indented fences, CRLF, lone CR, tabs, loose lists, quotes), with
  `stableCut` monotonic and equal from the previous cut and from zero;
  `healTail` cases with negative controls (`2**10 = 1024`, `a * b`,
  `snake_case`, fenced text untouched; final text never healed); stick-to-bottom
  transitions; `retryNow` opening exactly one connection with an injected
  timer/fetch; a retried step after `answer_reset`; the clipped-answer
  replacement; durations from server `ts`; the status-line signature and the
  one-time announcement; rave liveness with `partial`. Added after the
  adversarial review: a finished link at the end of the live tail stays a live
  link with no backslash (negative control: an escaped address shows one), and
  a bare URL after it is still neutralised; a turn waiting for an approval is
  live but not busy (negative control: a running turn without approvals stays
  busy).
- `command-center/tests/test_chat_ui_static.py`: login field conventions,
  accessible names on every button, hidden reasoning only ever dropped (with a
  negative control), reduced-motion / contrast / forced-colors CSS, every
  toggled class styled, imports resolve, `api.raw` signal passthrough, the
  lazy page manifest, `bcc-desktop --chat`, and it runs the node file above
  (skipped with a reason when node is missing). Added: the CSP meta with
  `require-trusted-types-for` first in `<head>`, no `<script>` without `src` and
  no `on…=` handlers (with a negative control), no HTML-string sinks in the chat
  code or `api.js`, the single `role=status` announcement node, the jump and
  «Сейчас» buttons' accessible names, reduced-motion and `content-visibility`
  rules, and `--install-shortcut --chat` / `--uninstall-shortcut --chat`
  refusing without touching the BOSSMAN shortcut (negative control: without
  `--chat` the install is still reached). Added after the adversarial review:
  with a live lock of the same build, `bcc-desktop --chat` still opens no second
  window but prints the chat address (negative control: without `--chat` no
  chat address), and the skip link and foreign anchors never reach `route()`.
  The new tests are appended at the end of the file, so the `pytest.skip` line
  (250) does not move.

## 6. Manual owner checklist for the bug test (Windows PC)

Each line: do it, compare with the expected result, note PASS/FAIL with a
screenshot. Nothing below was run in the cloud.

1. Close the BOSSMAN window, then run `bcc-desktop --chat`: the window shows the
   chat (or the chat login card). Log in with the token from the console/file.
   Then start BOSSMAN normally and run `bcc-desktop --chat` again: no second
   window opens (one browser profile); the console prints the chat address
   `http://127.0.0.1:8800/chat.html` and the way to it in the open window.
2. Open `http://127.0.0.1:8800/` → sidebar «Чат» → «Открыть чат»: the same chat
   opens; the classic shell still lands on «Главная».
3. New chat, write «Скажи привет одним словом», Enter: a user bubble appears,
   the Sphere animates, the answer streams (a local streaming model) or arrives
   whole; the status bar goes Streaming → Готово; the footer shows model,
   LOCAL/CLOUD, tokens and cost.
4. Press F5 in the middle of a long answer: the thread reopens and the live
   answer continues (no duplicated text).
5. Stop the server for ~10 s during an answer, start it again: «Переподключение…»,
   then the stream resumes without duplicates.
6. Start a long task and press Esc (or the red square): STOP; the turn shows
   «Остановлено владельцем» and «Отправить ещё раз».
7. Ask for something that needs a command (e.g. «покажи содержимое папки
   Документы»): a step card with a CMD chip appears; if an approval is needed,
   the inline card offers Разрешить / Отклонить; the decision is honoured and
   also visible in Command Center → Подтверждения.
8. Open the panel (Ctrl+.): Кратко / План / Действия / Проверка / Источники
   match what happened; no hidden reasoning text anywhere.
9. Model picker: Auto first, agents grouped with LOCAL/CLOUD and billing badges;
   an unusable cloud model is disabled with its reason. Choose Claude/Codex:
   the composer switches to Agentic Rave; start a rave and see the live card;
   STOP it.
10. Attach a .txt and a .png (button, drag & drop, Ctrl+V): chips with sizes,
    removable; the answer can use the text file.
11. Mic: if speech is not configured, the button is disabled with the reason; if
    configured, record, see the timer, stop → the recognised text lands in the
    input.
12. Rename, pin, move to project, archive, export Markdown; the sidebar groups
    and the project filter update. In CMD, `bossman resume <thread id>` (the id
    is in the address bar after `#t=`) continues the same conversation; a new
    `bossman chat` session appears in the web sidebar with a «CMD» mark.
13. Theme toggle (sun/moon) survives a reload; Windows «reduce animations»
    (Settings → Accessibility → Visual effects → Animation effects off) makes
    the Sphere static and hides the waveform.
14. Resize the window to ~800 px and ~400 px: no horizontal scrollbar; the
    panel overlays; the sidebar becomes icons.
15. DevTools (F12) → Console while you log in, send, stream, STOP, record with
    the mic and export: 0 «Content Security Policy» / «Trusted Types» errors
    from the chat itself (an error whose source is a browser extension, e.g.
    `chrome-extension://…`, is noted separately, not as a chat defect).
16. During a long streaming answer scroll up a little: the feed stays where you
    are, the ↓ button appears (with a dot as text arrives); press End or click ↓:
    back at the bottom and it follows again; the input keeps the focus. Select
    text in an earlier paragraph mid-stream: the selection is not lost.
17. Rename the chat title and press Esc while an answer streams: the rename is
    cancelled and the answer keeps streaming (no STOP). Same for Esc in the
    chat search.
18. Attach a file, then open another chat: the chip disappears with the note
    about re-attaching.
19. Copy a code block: the button shows «Скопировано ✓» for about 1.5 s.
20. Stop the server during an answer: the banner «Связь с сервером потеряна —
    задача продолжается на сервере.» with a countdown; start the server and
    click «Сейчас»: the stream resumes at once, the turn is not marked failed.
