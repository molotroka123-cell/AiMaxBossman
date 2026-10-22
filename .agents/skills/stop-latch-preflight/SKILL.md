---
name: stop-latch-preflight
description: Enforces fail-closed global STOP latch preflight checks before any computer action, exchange mutation, background compaction, or outbound notification.
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
    stop_file:
      type: string
      description: Path to the global STOP latch file
  required: [stop_file]
output_schema:
  type: object
  properties:
    stop_active:
      type: boolean
    verdict:
      type: string
      enum: [ALLOWED, BLOCKED_BY_STOP]
  required: [stop_active, verdict]
---

# STOP Latch Preflight (`stop-latch-preflight`)

Use this skill whenever implementing or auditing state-mutating endpoints, background tasks, exchange assistant operations, or computer-use actions.

## Triggers

- `stop`, `STOP`, `killswitch`, `fail-closed`, `stop_file`, `stop_reason`
- auditing `exchange_assistant`, `computer`, `agent_extras`, or `direct_gen` mutations

## Mandatory Rules

1. **Fail-Closed `os.stat` Check**:
   - Check `stop_file` via `os.stat()`. Only `FileNotFoundError` means STOP is inactive; any `PermissionError` or other `OSError` MUST be treated as STOP active (`True`).
2. **Store-Level & Handler-Level Enforcement**:
   - Enforce the STOP check both at the API/command handler boundary AND inside the store write method (`create_quote`, `confirm`, `reverse`, `set_rate`) so direct callers cannot bypass STOP.
3. **Zero Side Effects Under STOP**:
   - When STOP is active, return a clear refusal (`409` or `⛔ Остановлено владельцем (STOP)`) without creating quotes, deals, tasks, or outbound messages.
