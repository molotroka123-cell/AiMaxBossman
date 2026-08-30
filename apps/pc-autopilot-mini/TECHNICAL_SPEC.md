# PC Autopilot Mini — TECHNICAL SPEC V0.1

## Goal

Teach-once, deterministic-repeat PC macro utility using Bossman only for creation/repair.

## Product boundary

Приложение полностью standalone. BOSSMAN является optional control plane / intelligence layer.
Приложение не импортирует `bcc.*`, `bossman.*` и не дублирует Gateway, Policy, Approval, Secrets,
Memory, Browser или Computer Operator.

## Runtime

- Python >= 3.11
- FastAPI control surface
- localhost default port `8914`
- local-first storage
- SQLite recommended for production foundation
- no cloud dependency for basic operation

## Core job types

`record`, `compile_macro`, `validate_macro`, `run_macro`, `repair_macro`, `audit_run`

## Control contract

- GET `/health`
- GET `/capabilities`
- GET `/metrics`
- POST `/api/jobs`
- GET `/api/jobs`
- GET `/api/jobs/{job_id}`
- POST `/api/jobs/{job_id}/cancel`
- GET `/api/jobs/{job_id}/artifacts`

## Data model

Minimum:
- Job
- Artifact
- AuditEvent
- AppSettings
- domain-specific entities

Job states:
`queued -> running -> completed | failed | cancelled`

Любой внешний side effect должен иметь idempotency key и честный final state.

## BOSSMAN integration

1. Bossman discovers `app.manifest.yaml`.
2. It may call app HTTP operations.
3. Heavy intelligence can be delegated to Bossman.
4. Reverse direction is prepared by `bossman_bridge.py`:
   app writes a task envelope atomically into a local inbox.
5. A future generic BOSSMAN adapter may claim the task and return a result.
6. This app must still function when BOSSMAN is offline.

## Security

Permissions are manifest-declared.
Destructive / external / financial / credential operations never become AUTO by accident.
Secrets must never be committed or serialized into task files.
No LLM output is directly converted into arbitrary shell commands.

## Persistence roadmap

Foundation currently uses in-memory jobs only.
Next implementation step:
- SQLite jobs
- durable audit
- artifact hashes
- crash-safe resume
- schema migrations

## Testing roadmap

- manifest contract
- standalone import independence
- health truthfulness
- jobs lifecycle
- cancellation
- persistence
- idempotency
- permission policy
- secret redaction
- restart recovery
- Bossman discovery
- Local Task Exchange atomic claim/result
- failure injection

## 5 improvements for approval

1. **Teach-by-demonstration** — Записать действия пользователя и превратить их в устойчивый workflow с selectors и expected-state checks.
2. **Selector self-healing** — При изменении UI не угадывать молча: найти вероятный новый selector, поставить PAUSED_NEEDS_REPAIR и запросить approval.
3. **Dry-run simulator** — Показывать будущие действия и затрагиваемые файлы/окна до реального запуска.
4. **Versioned macros** — Каждый workflow имеет версии, diff, rollback и last-known-good.
5. **Triggers without AI** — Расписание, появление файла, запуск программы или hotkey могут запускать детерминированный макрос без LLM.


## V0.7 implemented
Macro storage; validation; versioning; variable expansion; dry run; deterministic safe local executor; strict pause for UI steps requiring existing Bossman Computer Operator; repair proposal handoff; audit.
## Remaining major work
teach-by-demonstration recorder, real Stage13 bridge, selector observation/repair evidence, trigger daemon, richer rollback.


## V1.0 implementation pass

This package now includes a minimal embedded HTML status UI, durable SQLite storage,
domain endpoints, app-side task-result client, richer local algorithms, and safety-first
Bossman handoff points. Live external providers remain environment-dependent and are
not falsely marked complete.
