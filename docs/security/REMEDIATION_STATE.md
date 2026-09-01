# SECURITY REMEDIATION — LIVE STATE (чекпоинт-лог)

START_SHA=09ab6160cf04719f149b444c95c202ca72818d17
HOST=Linux container, no GPU, docker daemon НЕ запущен (F-009 container E2E = NOT_TESTED_ON_THIS_HOST), PG16 live @5433
PHASE=0 ingest complete → remediation in progress

## План владения файлами (параллельная работа)
- LEAD: bcc/engine.py, bcc/tools.py, bcc/features/tools_terminal.py, bcc/v2/terminal_control.py, bcc/features/review_gate.py, bcc/v2/verification.py(new)
  → F-009, F-012, F-013 (+ approval/result preview redaction), терминальная часть F-011
- AGENT A (bcc): F-014 tools_mcp.py + MCP spawn allowlist; F-010 browser policy; F-011 browser/opencode session ownership; F-015 self-asserted flags (features/terminal.py, browser.py, snapshot.py, nl_orchestra.py, api.py PATCH /agents)
- AGENT B (core): F-008 gateway fail-closed + embeddings + audit by resolved route; F-006/F-007 untrusted marker (context.py, runner.py); F-004 http SSRF policy; F-005 projects runner enforcement; 429 bounded backoff in gateway/client.py; gateway RedactionFilter
- AGENT C (bcc): F-016 router/forks fail-closed; F-017 discovery validate_url + taskxchange app_id; F-006 bcc facts external_output; BUG-005 discovery hang
- AGENT D: BUG-004 auth-redteam loop; F-018 dead-code dispositions (wire analysis/fileintel; permissions deny-list; code_index._within; context_os; sandbox/secrets; mask_enc); resource sampler ollama runner; bcc events redaction

## Чекпоинты
- [T0] ingest: 18 findings прочитаны, PoC .agents/redteam/* на месте, код всех границ прочитан
