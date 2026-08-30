# Travel Architect / Deal Hunter — TECHNICAL SPEC V0.1

## Goal

Browser-assisted travel deal hunter optimizing complete trip cost instead of headline price.

## Product boundary

Приложение полностью standalone. BOSSMAN является optional control plane / intelligence layer.
Приложение не импортирует `bcc.*`, `bossman.*` и не дублирует Gateway, Policy, Approval, Secrets,
Memory, Browser или Computer Operator.

## Runtime

- Python >= 3.11
- FastAPI control surface
- localhost default port `8913`
- local-first storage
- SQLite recommended for production foundation
- no cloud dependency for basic operation

## Core job types

`search_trip`, `compare_packages`, `optimize_dates`, `build_itinerary`, `watch_price`, `trip_report`

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

1. **Multi-origin optimizer** — Одновременно сравнивать Прагу, Вену, Берлин и другие разрешённые аэропорты с учётом дороги до них.
2. **True trip cost engine** — Считать багаж, трансфер, resort fee, страховку, питание, парковку и локальный транспорт.
3. **Package-vs-DIY arbitrage** — Сравнивать туроператора с самостоятельным flight+hotel и показывать реальную экономию/риск.
4. **Price watch & trigger** — Лёгкий периодический watcher без LLM: уведомлять только при достижении заданной цены/качества.
5. **Constraint intelligence** — Погода, визовые требования, сезонность, события, пересадки, ночные прилёты и качество района как hard/soft constraints.


## V0.7 implemented
Trip constraints; offers; true-price fee aggregation; ranking; flexible-date candidates; package-vs-DIY arbitrage; price-watch evaluation; Bossman browser-search task handoff; SQLite persistence.
## Remaining major work
live travel-source adapters/browser recipes, scheduled watcher, weather/visa/event enrichment, itinerary UI.


## V1.0 implementation pass

This package now includes a minimal embedded HTML status UI, durable SQLite storage,
domain endpoints, app-side task-result client, richer local algorithms, and safety-first
Bossman handoff points. Live external providers remain environment-dependent and are
not falsely marked complete.
