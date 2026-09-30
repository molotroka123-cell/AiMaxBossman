# PostgreSQL run contracts

Repository follow-up to `PR60_REQUIRED_SEMANTIC_INTEGRATION_20260910.md`.
Real PostgreSQL acceptance is now a required dedicated GitHub Actions job,
not deferred automatically to an owner host because this local environment lacks
PostgreSQL. Its result must still be read on the final source SHA.

Fixed defects:

- The PostgreSQL terminal-status branch had no guard and referenced an undefined
  `log`, raising `NameError` during setup. Its attempted warning also contained the
  full database URL. Both are removed; unsupported dialects fail with a fixed
  credential-free error. No URL is emitted by either guard installer.
- SQLite allowed `completed/failed -> NULL` because `<>` with SQL NULL is unknown.
  SQLite now uses `IS NOT`, PostgreSQL uses `IS DISTINCT FROM`; both prevent erasing
  the terminal outcome. Ordinary queued/leased/running transitions, same-status
  writes, later receipt/result writes and new retry rows remain valid.
- Existing SQLite triggers are upgraded under an explicit `BEGIN IMMEDIATE`.
  SQLAlchemy's logical transaction alone does not start SQLite DDL transactions.
  Injecting a failure between DROP and CREATE previously removed the old guard;
  the regression now requires rollback to retain its protection.

`.github/workflows/postgres-contracts.yml` has read-only repository permissions,
uses `github.event.pull_request.head.sha || github.sha` for checkout and its
source manifest, and starts a real `postgres:16` service. Python 3.11, 3.12 and
3.14 match the current Command Center matrix. `asyncpg` is installed explicitly.
No existing Command Center workflow was changed.

`BCC_REQUIRE_POSTGRES=1` makes missing configuration fail. A configured service
connection failure is never skipped or caught as success. The proof requires four
live PostgreSQL cases: captured provenance, completed outcome, failed outcome,
and valid transactions. All run in newly created random schemas, which are
removed afterwards; existing owner tables are untouched. Every denied mutation
is in a transaction that first inserts into an independent control table. Tests
independently reread both tables and require the entire transaction rolled back.
Both guards are installed twice to verify idempotent setup.

The workflow rejects skipped/failed/error cases and missing live cases before
writing `postgres-evidence.json`. It retains that file, JUnit XML and the exact
source SHA. A failure cannot produce a PASS manifest. A local unit pass does not
substitute for that artifact.

Local negative controls reproduced NULL outcome erasure, the undefined logger,
and non-atomic SQLite DDL rollback. Local SQLite/provenance/shutdown regressions
passed; live PostgreSQL cases remain unexecuted locally. GitHub Actions result:
**PENDING**, to be recorded after the parent pushes and reads the actual run.

Owner/developer invocation when a permitted PostgreSQL test instance is available:

```bash
BCC_REQUIRE_POSTGRES=1 python -m pytest -c command-center/pyproject.toml \
  command-center/tests/test_run_provenance_postgres.py \
  command-center/tests/test_v5_terminal_run_immutability.py -q
```

Set `BCC_TEST_POSTGRES_URL` to a `postgresql+asyncpg` DSN before this command; do
not paste credentials into reports. GitHub Actions supplies its own ephemeral
service credentials, so owner database credentials are not required for CI.
