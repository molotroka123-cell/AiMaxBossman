# Jeff 2.0 - proactive (Proactive Companion, order 80)

File: `bcc/pit/j2/proactive.py`. Tests: `tests/test_jeff_2_proactive.py`.

## What the participant gets

| They write | Jeff does |
|---|---|
| `напомни завтра в 10 позвонить маме` | stores a reminder, confirms "Хорошо, напомню завтра в 10:00: «позвонить маме»." |
| `мои напоминания`, `отмени напоминание 2`, `отмени все напоминания` | lists / cancels (numbers are per listing) |
| `стоп напоминания` | one-message opt-out: everything scheduled is cancelled, nothing is sent first |
| `включи напоминания` | explicit consent to follow-ups and thread questions |
| `включи сводку` / `выключи сводку` | daily digest on/off |
| `мой часовой пояс UTC+5`, `тихие часы с 23 до 7` | per-participant timezone and quiet hours |

Reminders the participant asked for are delivered unless they opted out. Everything Jeff would start by itself
(follow-ups on long tasks, "как прошло собеседование?", the digest) needs the explicit consent above, respects quiet
hours (default 22-08 local), at most 3 such messages a day and at least 1 hour apart, and carries the opt-out hint.

## Time phrases

Russian parser with a labelled set in the tests (33 phrases, now = Tue 2026-09-29 12:00, UTC+3): `завтра в 10`,
`через 2 часа`, `через полчаса`, `через пару часов`, `в пятницу в 10`, `15 октября в 9`, `завтра вечером`,
`в 22.15`, `в 10 вечера`, ... Rules worth knowing: an hour 1-6 without "утра/вечера" means the afternoon (`в 5` =
17:00); only a time means today, or tomorrow if it has passed; only a date means 09:00; the same weekday as today
means next week; times in the past and hours above 23 are rejected (Jeff asks for a time instead of guessing).

## Follow-ups (long tasks and open threads)

* Task states from `bcc/pit/tasks.py`: `WAITING_INPUT` (after 3 h, once more after 24 h), `WAITING_APPROVAL`
  (same), `UNKNOWN_OUTCOME` (after 30 min: "подтверди, что произошло"). `DONE`/`FAILED` only when the participant
  set `notify_finished`. A new wait on the same task is a new follow-up; when the task moves on, pending ones
  are cancelled. Goals are shortened to 80 characters and secret-redacted.
* Open threads: "завтра у меня собеседование" (собеседование, экзамен, встреча, операция, защита, презентация,
  поездка, свидание, переговоры, интервью) schedules one question for that evening. Third-person events
  ("у Пети") are ignored. Only with consent.

## Guarantees

* Persistence: `<personalities>/<person_key>/proactive/schedule.json` and `prefs.json`, atomic fsynced writes.
* Idempotency: every item has a key (`sha256`, per message id / task state / digest date). Adding the same key twice
  is a no-op, also after compaction (`done_keys`). `sending` is persisted before the sender is called.
* Crash or timeout after a possible send: never repeated blindly. The sender's optional `already_sent(key)` decides,
  otherwise the item becomes `unknown` (visible in the store, not resent). `False` from the sender means "certainly
  not sent" and is retried with backoff (3 attempts).
* Surface that cannot push (Jeff window): the item becomes `missed` and is appended to the next reply once.
* Stale items: a reminder up to 48 h late is sent with "(опоздало, было в ...)", older ones expire; follow-ups
  expire after 48 h.
* Isolation: every read and write is scoped to one `person_key`; revoked participants get nothing.

## Wiring

`create(runtime)` builds a store over `runtime.vault.root`, tasks over `TaskStore(runtime.home)`, and a
`TelegramSender` that maps the person key back to a private chat through `settings.people` (and workers spawned for
the open allowlist); the Telegram id is never stored. The background loop ticks every 30 s and is started by
`ParticipantRuntime._j2_lifecycle` (one small addition in `runtime.run`). Timezone default: `BOSSMAN_JEFF_TZ_MIN`
(minutes, default 180).

Status keys: `name, version, pending, participants, sent, deferred, failed, running, last_tick` (counts only).

## Known gaps

* Timezone is per participant but has no geolocation; the default is Moscow.
* No recurring reminders ("каждый день") yet; the digest is the only recurring item.
* The Jeff window has no push channel; missed reminders appear on the next turn.
