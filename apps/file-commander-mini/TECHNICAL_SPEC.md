# File Commander Mini — TECHNICAL SPEC V0.1

## Goal

Tiny deterministic file hygiene utility with preview-first operations.

## Product boundary

Приложение полностью standalone. BOSSMAN является optional control plane / intelligence layer.
Приложение не импортирует `bcc.*`, `bossman.*` и не дублирует Gateway, Policy, Approval, Secrets,
Memory, Browser или Computer Operator.

## Runtime

- Python >= 3.11
- FastAPI control surface
- localhost default port `8911`
- local-first storage
- SQLite recommended for production foundation
- no cloud dependency for basic operation

## Core job types

`scan`, `plan`, `rename`, `deduplicate`, `organize`, `archive`

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

1. **Semantic sorter** — Лёгкая локальная классификация файлов по проектам/темам, без тяжёлого LLM на каждом файле.
2. **Safe rollback snapshots** — Перед массовым rename/move сохранять manifest операции и уметь полностью откатить её.
3. **Smart duplicate sets** — Хеши + near-duplicate detection для фото/документов, но удаление только после preview/approval.
4. **Project bundle creator** — Одна команда собирает связанные файлы проекта в portable bundle с индексом.
5. **Watch-folder mode** — Опционально следить за Downloads/Inbox и только предлагать действия, не применять без правил.


## V0.7 implemented
Safe-root confinement; recursive scan; exact duplicate hashing; organize plan; preview-first apply; rollback batch manifest; SQLite audit/jobs.
## Remaining major work
near-duplicate media similarity, watch-folder service, richer smart project classification, polished UI.


## V1.0 implementation pass

This package now includes a minimal embedded HTML status UI, durable SQLite storage,
domain endpoints, app-side task-result client, richer local algorithms, and safety-first
Bossman handoff points. Live external providers remain environment-dependent and are
not falsely marked complete.
