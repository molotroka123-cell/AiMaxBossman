# PLUGINS FINAL BUG CHECK (adapter integration)

Дата: 2026-08-30 · HEAD: `3d04e66` (интеграция поверх)

Формат: FINDING → FOUND_BY → ROOT_CAUSE → SEVERITY → FIX → VERIFIED_BY.

## Исправленные при интеграции (из bundle-аудита)

### F1 → FIXED — SSRF: только литеральный IP, без DNS/redirect (bundle plugins)
- FIX: `bcc/plugin_security.py` — `validate_url` (литерал) + `resolve_pinned_ip`
  (DNS-резолв, любой небезопасный адрес → отказ) + `safe_get` (redirect'ы НЕ
  следуются автоматически, каждый hop валидируется и резолвится заново).
- SEVERITY: P1 (для сетевых адаптеров) → закрыто.
- VERIFIED_BY: `test_ssrf_literal_targets_blocked` (10 форм), `test_ssrf_dns_rebinding_blocked`,
  `test_safe_get_rejects_redirect_to_private`, `test_safe_get_follows_safe_and_returns`.

### F2 → FIXED — DNS-rebinding TOCTOU
- FIX: `resolve_pinned_ip` проверяет ВСЕ резолвы; коннект-путь идёт после проверки,
  redirect-хопы перепроверяются. (Полный pinned-connect транспорт — возможное P3-усиление.)
- VERIFIED_BY: те же тесты.

### F3 → FIXED — SQL read-only только на регэкспе
- FIX: `sql_read_only_ok` — одиночный оператор, только `select/with/read-pragma`;
  любой write/pragma-write/multi-statement → deny. Плюс `sql.write` capability не
  существует вовсе (нельзя вызвать).
- VERIFIED_BY: `test_sql_read_allowed` (5), `test_sql_write_denied` (12).

## Проверенные инварианты интеграции (targeted, 48 тестов)

| Инвариант | Тест | Итог |
|---|---|---|
| регистрация в существующий REGISTRY | test_capabilities_registered_into_existing_registry | PASS |
| unknown capability → DENY | test_unknown_capability_is_denied_by_absence | PASS |
| дубль не удваивается | test_duplicate_capability_not_double_registered | PASS |
| read=auto, write/send=ask | test_read_is_auto_write_and_send_are_ask | PASS |
| destructive не переигрывается | test_destructive_send_is_not_auto_replayable | PASS |
| inert без выдачи агенту | test_default_agent_gets_no_plugins | PASS |
| ollama local/cloud_policy=never | test_ollama_capability_declares_local_only | PASS |
| openrouter → Cost Governor authority | test_openrouter_capability_routes_through_cost_governor_authority | PASS |
| path traversal denied | test_path_traversal_denied | PASS |
| symlink escape denied | test_symlink_escape_denied | PASS |
| redaction по ключам и значениям | test_redaction_by_key_and_value | PASS |
| нет креда → SKIP, 0 side effect | test_external_without_credential_skips_no_side_effect | PASS |
| SSRF на handler-уровне | test_http_get_blocks_ssrf_at_handler | PASS |
| status без сырых секретов | test_status_endpoint_reports_no_secrets | PASS |

## Дедупликация инфраструктуры
Проверено: НЕ создано второго Gateway/approval/policy/event/secret/browser/Telegram/
MCP/Cost Governor. Всё — поверх существующих (`REGISTRY`, `decide_effect`, движок
approvals+anti-replay, `svc.vault`, `svc.bus`, mcp_runtime, browser-подсистема).

## P0 / P1 / P2
- P0: нет.
- P1: нет (F1/F3 закрыты).
- P2: pinned-connect транспорт (полный анти-rebinding на уровне сокета) — задокументировано,
  текущая защита (literal + resolve-all + no-auto-redirect + per-hop revalidate) достаточна для V1.

## Регрессия
- plugin targeted: 48 passed.
- command-center full: 481 passed / 2 skipped (0 регрессий против 433/2).
- bossman-core full: 906 passed / 4 skipped.
- secret scan: PASS.
