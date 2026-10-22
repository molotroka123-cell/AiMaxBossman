---
name: utf8-mojibake-guard
description: Prevents CP1251/CP866/UTF-8 mojibake in Russian UI strings, subprocess output, and JSON/YAML files on Windows by enforcing explicit encoding="utf-8" and UTF-8 subprocess streams.
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
    target_path:
      type: string
      description: File or directory to scan for UTF-8 and mojibake compliance
  required: [target_path]
output_schema:
  type: object
  properties:
    mojibake_found:
      type: boolean
    verdict:
      type: string
      enum: [PASS, FAIL]
  required: [mojibake_found, verdict]
---

# UTF-8 & Mojibake Guard (`utf8-mojibake-guard`)

Use this skill whenever reading or writing Russian text, HTML/JS UI files, markdown docs, or running subprocesses on Windows.

## Triggers

- `utf8`, `mojibake`, `кириллица`, `кодировка`, `cp1251`, `UnicodeDecodeError`
- editing Russian UI labels in `apps/bossman-mobile`, `command-center`, or `apps/war-terminal`

## Mandatory Rules

1. **Explicit UTF-8 on Every File I/O**:
   - Always pass `encoding="utf-8"` to `Path.read_text()`, `Path.write_text()`, and `open()`. Never rely on the default Windows locale (`cp1251` / `cp1252`).
2. **Subprocess UTF-8 Discipline**:
   - Launch Python with `-X utf8` or `PYTHONIOENCODING=utf-8` and pass `encoding="utf-8", errors="replace"` to `subprocess.run`.
3. **Zero Mojibake Verification**:
   - Verify that Russian strings contain valid Cyrillic codepoints (`U+0400..U+04FF`) and no `Р С`/`С‚` double-encoded sequences or `U+FFFD` replacement characters.
