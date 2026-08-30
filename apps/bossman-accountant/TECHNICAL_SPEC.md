# Bossman Accountant — TECHNICAL SPEC V0.1

## Goal

Standalone local-first accounting, finance reporting and owner analytics app.

## Product boundary

Приложение полностью standalone. BOSSMAN является optional control plane / intelligence layer.
Приложение не импортирует `bcc.*`, `bossman.*` и не дублирует Gateway, Policy, Approval, Secrets,
Memory, Browser или Computer Operator.

## Runtime

- Python >= 3.11
- FastAPI control surface
- localhost default port `8910`
- local-first storage
- SQLite recommended for production foundation
- no cloud dependency for basic operation

## Core job types

`import_transactions`, `categorize`, `build_pnl`, `build_cashflow`, `anomaly_scan`, `owner_report`

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

1. **CZ/SK country pack** — Добавить локальные правила, налоговый календарь, форматы банков и экспорт для бухгалтера. Не смешивать country logic с ядром.
2. **Invoice/receipt ingestion** — OCR + извлечение поставщика, НДС, суммы, даты, валюты; всегда сохранять confidence и оригинал.
3. **Owner Morning Digest** — Автоматический краткий отчёт владельцу: выручка, расходы, маржа, runway, аномалии, неоплаченные обязательства.
4. **Reconciliation assistant** — Сопоставление банковских транзакций со счетами/чеками и очередь unmatched items для ручной проверки.
5. **Forecast scenarios** — What-if по расходам, росту выручки, зарплатам и налоговым резервам с несколькими сценариями.


## V0.7 implemented
SQLite ledger storage; durable jobs/audit; transaction import; heuristic category engine; P&L; cashflow; owner digest; health score; what-if forecast; simple invoice/transaction reconciliation.
## Remaining major work
CSV/PDF adapters, invoice OCR, CZ/SK country pack, richer reconciliation, scheduled digest/UI.


## V1.0 implementation pass

This package now includes a minimal embedded HTML status UI, durable SQLite storage,
domain endpoints, app-side task-result client, richer local algorithms, and safety-first
Bossman handoff points. Live external providers remain environment-dependent and are
not falsely marked complete.
