# Jeff 2.0 - insights (Owner Insights, order 95)

Files: `bcc/pit/j2/insights.py` (collector + module), `bcc/features/jeff_insights.py` (owner API),
`ui/pages/jeff_insights.js` (page «Jeff · обзор», manifest entry in `ui/pages/index.js`).
Tests: `tests/test_jeff_2_insights.py` (data, digest, API, static UI wiring, real-Chromium page checks).

## What the owner sees

| Block | Source (nothing new is stored except derived caches) |
|---|---|
| Participants table: label, memory consent, messages, facts, last activity, tasks by state, pending reminders, quality, "has narrative" | `personalities/<key>/{consent.json,facts.jsonl,raw/}`, `TaskStore`, proactive and quality stores, participant profile (access) |
| Narratives: path, model, run, date | `passport-checkpoints/narratives/<key>.json` (Master Parser 2.0) |
| Health: Telegram and window heartbeat (up / stale / stopped / absent), queue, model_guard, module breakers, recent error kinds, tasks by state | `heartbeat.json`, `web/heartbeat.json`, `companion.sqlite3` (read-only), `j2-status.json`, `logs/runtime_error.jsonl`, `tasks/` |
| Trends: daily replies, success rate, latency, quality, tasks done/failed (default 14 days, max 90) | `logs/route_log.jsonl`, quality `scores.jsonl`, `tasks/*/events.jsonl` |
| Weekly digest for the Pult | computed from the above |

`model_guard` status is optional: the Jeff process writes a scalar-only snapshot of every module's status to
`pit-v1.7/j2-status.json` (the `insights` background loop, every 60 s); the Command Center process reads it. Without
the module, health shows `model_guard: нет данных` and never invents a state. A `degraded`/`down` guard or an open
breaker turns the overall health flag off.

## Owner-only API

Mounted under `/api` with the normal owner auth (participants have no Command Center session):

* `GET /jeff-insights/overview`, `/participants`, `/narratives`, `/trends?days=`, `/digest`
* `GET /jeff-insights/narratives/{key}`: the narrative text of ONE participant, only here
* `POST /jeff-insights/digest/send`: puts this ISO week's digest into the Pult outbox once (`already: true` afterwards)

Participants are shown with the owner's labels (from the same source as the Jeff settings panel, so display names
appear only in owner responses). Lists carry relative paths, never absolute ones, no Telegram ids and no message,
fact or narrative text. Jeff not configured on this machine is a normal 200 with `configured: false` and empty lists.

## Weekly digest

A short Russian text: participants and active ones, replies this week against the week before, success rate, average
latency, quality from the checks, task counts, health, and an "Внимание" list (no heartbeat, queue above 10, unreadable
queue, model_guard problem, errors in the log, a participant with at least 5 scored replies below 0.50 shown as
`#a1b2c3`). Every number is computed from the files above; with no data it says so. It contains no participant text
and no full keys. Delivery: once per ISO week (marker in `insights/state.json`); the loop sends it on Monday from
09:00 local time (`BOSSMAN_JEFF_TZ_MIN`, default 180) and retries on the next tick when the Pult sender fails.

## UI page

Registered lazily (`jeff-insights`, "more" navigation, section "brains"). Empty state is a normal page (no errors, no
4xx). The "Нарратив" button is disabled with an explanatory title when there is no narrative; the digest buttons show
the result or a toast; the modal with a narrative is the only place where narrative text appears.

Status keys of the module: `name, version, running, errors, last_snapshot_at, last_digest_week`.

## Known gaps

* The Pult (owner Telegram companion) has no inbound channel for module messages yet, so the default sender appends
  to `insights/pult-outbox.jsonl` and writes `insights/weekly/<week>.txt`. Wiring the companion to read the outbox (or
  passing a real `pult_sender` to the module) is the last step; the digest content and idempotence are done.
* Trends use the route log (model calls), not every chat turn; the window surface's replies are counted only when the
  route log has them.
* The participants table reads directories on each call (capped at 200 participants).
