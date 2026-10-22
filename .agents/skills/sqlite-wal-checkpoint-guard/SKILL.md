---
name: sqlite-wal-checkpoint-guard
description: Enforces SQLite WAL mode, busy_timeout>=5000ms, and clean connection teardown across Bossman stores to prevent SQLITE_BUSY and unawaited aiosqlite rollback CancelledError on Windows.
license: MIT
compatibility: BOSSMAN, OpenCode, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
  provenance: north-star-orchestra-20261023
required_tools: [code.search, terminal.run]
permissions: [read, exec]
input_schema:
  type: object
  properties:
    db_path:
      type: string
      description: Path to SQLite database or store module
  required: [db_path]
output_schema:
  type: object
  properties:
    journal_mode:
      type: string
    busy_timeout_ms:
      type: integer
    verdict:
      type: string
      enum: [PASS, FAIL]
  required: [journal_mode, busy_timeout_ms, verdict]
---

# SQLite WAL & Checkpoint Guard (`sqlite-wal-checkpoint-guard`)

Use this skill whenever creating, auditing, or debugging SQLite stores (`sqlite3` or `aiosqlite` / SQLAlchemy) on Windows to prevent `database is locked` (`SQLITE_BUSY`) and unclosed connection pool warnings.

## Triggers

- `sqlite`, `wal`, `busy_timeout`, `SQLITE_BUSY`, `database is locked`, `aiosqlite`
- designing or fixing a local store in `bossman_shared` or `command-center`

## Mandatory Rules

1. **Always Enable WAL & Busy Timeout on Connect**:
   - Execute `PRAGMA journal_mode=WAL;` and `PRAGMA busy_timeout=5000;` (or `timeout=5.0` in `sqlite3.connect`) immediately upon opening any SQLite connection.
2. **Immediate Transactions for Compare-And-Swap Writes**:
   - Use `BEGIN IMMEDIATE` when validating state before mutating (e.g., confirming a quote into a deal or reserving budget) so concurrent writers serialize deterministically.
3. **Clean Engine Disposal**:
   - Always `await engine.dispose()` before event loop shutdown on Windows to avoid `aiosqlite` rollback `CancelledError`.
