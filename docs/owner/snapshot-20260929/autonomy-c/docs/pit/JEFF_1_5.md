# Jeff 1.5 "Memory & long tasks"

Status: candidate on `feat/jeff-1.5` (from the 1.9 freeze line plus Master Parser 2.0). Covered
by tests with fakes only; not measured on real participants. Schema constant:
`JEFF_PASSPORT_SCHEMA = "jeff.passport/1"` (`bcc/pit/passport.py`). No global version is bumped.

## 1. Versioned passport with evidence

Every fact row keeps all its old fields and gains an additive `passport` envelope:
source (`message` / `voice` / `command` / `master_parser` / `legacy`, reference, date),
confidence, usage scope (`own_chat`, `own_chat_local_only` for sensitive), consent snapshot,
freshness (computed at read time: fresh / stale / expired), version and correction history
(previous value, actor, surface, time; at most 20 entries).

Four layers, separate files in the participant's own namespace, never merged:

| layer | file | notes |
| --- | --- | --- |
| conversation events | `raw/events.jsonl` | unchanged, consent-gated |
| confirmed facts | `facts.jsonl` | envelope per row |
| communication style | `style.json` | fed by Master Parser narratives, labelled inference, versioned |
| verified Jeff procedures | `procedures.jsonl` | accepted only with verifier and evidence reference |

Style reaches the model only through the same gates as facts (memory consent, personalisation
on, remote model only with remote-personalisation consent).

### Migration notes

- Old rows are readable without any migration; readers upgrade them in memory.
- `passport.migrate_person` / `migrate_all` rewrite `facts.jsonl` once, keep a backup
  `facts.jsonl.pre-passport-1`, verify row count and every original key/value, and roll back
  from the backup on any mismatch. Idempotent. Each participant is migrated on its own; the
  participant's `/passport` view triggers it lazily. Rollback: restore the backup file.
- `consent.json` gains `personalization_enabled` (default true, so old files behave as before).

## 2. Participant commands (survive restart, consent-gated, audited)

Telegram: `/passport` (view with evidence), `/correct`, `/forget`, `/personalization on|off`,
`/revoke_consent` (existing `/pause_memory`, `/privacy`, `/delete_me` unchanged).
Jeff window: `GET /api/jeff/passport`, `POST /api/jeff/passport/consent`
(`revoke`, `personalization_on`, `personalization_off`), plus the existing memory
correct/delete endpoints. All state lives in the participant's own files.

- View needs memory consent; enabling personalisation needs memory consent; restricting or
  revoking is never blocked.
- Revoke clears every consent flag, bumps the memory epoch (in-flight turns cannot write),
  keeps stored data until the participant deletes it.
- Each action writes a value-free row to `memory_audit.jsonl` (`consent`, `passport_view`,
  `correct`, `delete`, ...) and consent flag changes go to `consent_history.jsonl`.

## 3. Long tasks (`bcc/pit/tasks.py`)

Task record: goal, steps, done, awaited input/approval, constraints, build SHA, approval refs,
per-action ledger. States `PLANNED, RUNNING, WAITING_INPUT, WAITING_APPROVAL, DONE, FAILED,
UNKNOWN_OUTCOME`. Stored under `pit-v1.7/tasks/<owner>/` with the vault's atomic writer; the
store runs nothing external itself (callers pass executors), so there is no second engine.

External-action rule: idempotency key per action, `STARTED` persisted before the call,
`COMPLETED` after. After a restart a `STARTED` action is never blindly repeated: the verifier
says whether it happened; no verdict means `UNKNOWN_OUTCOME`, which only
`confirm_unknown(actor=participant|owner)` can resolve. Executors that raise anything but
`ActionNotPerformed` count as unknown. Wiring into Telegram sends is the remaining step (see
gaps): the store and its verifier contract are ready, no runtime path uses tasks yet.

## 4. Learning hooks

`bcc/pit/learning_counters.py`: per build SHA counters (ok, fail, interventions, latency,
cost). Task terminal states record automatically. `compare` says `insufficient` under 5
samples per side; memory hits are never counted as learning.

## Revert

Do not merge the branch. Data-level: restore `facts.jsonl.pre-passport-1`; delete `style.json`,
`procedures.jsonl`, `consent_history.jsonl`, `tasks/`, `learning-counters.json` (all derived or
additive).
