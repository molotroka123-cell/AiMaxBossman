---
name: secret-hygiene
description: Pre-push secret scan and daily secret storage hygiene check across Bossman directories (verifies DPAPI encryption, flags plaintext .env/token files by filename and signature only without ever reading or printing secret values).
license: MIT
compatibility: BOSSMAN, OpenCode, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
  provenance: pc-defense-two-lines-20261022
required_tools: [terminal.run, memory.write]
permissions: [read, exec]
input_schema:
  type: object
  properties:
    target_path:
      type: string
      description: Optional file or directory path to scan with ci_secret_scan.py
  required: []
output_schema:
  type: object
  properties:
    verdict:
      type: string
      enum: [PASS, FAIL]
    plaintext_files_count:
      type: integer
    dpapi_files_count:
      type: integer
  required: [verdict, plaintext_files_count]
---

# Secret Hygiene & Pre-Push Guard (`secret-hygiene`)

Use this skill before every `git push` and during daily security checks to ensure zero API keys, bot tokens, database URIs, or plaintext `.env` files exist in git or unencrypted on disk.

## Triggers

- ключи
- секреты
- push
- проверь ключи
- secret scan
- утечка токенов

## Deterministic Workflow

1. **Pre-Push Repository & File Scan (`terminal.run`)**:
   - Run `python tools/ci_secret_scan.py` (or pass explicit candidate files `python tools/ci_secret_scan.py <file>`) to detect OpenAI/Anthropic/OpenRouter/Google/GitHub/Telegram/AWS keys, Railway/Zeabur/LiteLLM tokens, DB connection strings with credentials, and high-entropy tokens.
2. **DPAPI vs Plaintext Secret Inventory (`terminal.run`)**:
   - Inspect file signatures under `%LOCALAPPDATA%\Bossman` and worktrees by checking only the magic header (`\x01\x00\x00\x00\xd0\x8c\x9d\xdf` = `DPAPI_BLOB` vs `PLAINTEXT`).
   - Report only file paths, byte sizes, and `DPAPI_BLOB` / `PLAINTEXT` status via `memory.write`.
3. **Owner Alerting**:
   - If any `PLAINTEXT` key file or `ci_secret_scan` finding is detected, notify the owner to migrate the file into `provider-keys.dpapi` and shred the plaintext copy.

## Safety & Permission Rules

- Never print, log, or transmit the contents or values of any `.env`, token, or key file.
- Never delete or overwrite key files without the owner's explicit "да".
