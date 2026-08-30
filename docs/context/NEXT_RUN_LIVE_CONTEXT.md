# NEXT RUN — LIVE CONTEXT (обновляется по ходу работы)

Назначение: если лимит модели кончится посередине — следующая модель продолжает
отсюда без перечитывания всего репо.

## HEAD
- START этого рана: `8c1c5d6` (remote tip; мой предыдущий plugin-адаптер `aa7f1a0` — предок).
- Ветвь: `claude/bossman-control-v03-43igbk`. NO force push.

## Цель рана (8→9)
Закрыть слабые места vs OpenCode: Code intelligence (~6.0), Benchmark evidence (~5.5),
Plugins (закрыто в aa7f1a0), Coding workflow. Адаптировать заготовки из
`BOSSMAN_NEXT_RUN_8_9_PACK.zip` в СУЩЕСТВУЮЩИЙ registry/tool loop — без второго фреймворка.

## Сделано в этой сессии (ранее, уже в remote)
- `aa7f1a0` — plugin-адаптеры 13 коннекторов поверх bcc.tools.REGISTRY + `bcc/plugin_security.py`
  (SSRF/path/redaction). 48 тестов. command-center 481 passed.
- Pythia-фиксы (`b0a5a0c`), RC-gate отчёты (`f4a37d9`), Cost Governor/approvals/FactStore — зелёные.

## Делаю сейчас (in progress)
- [ ] `bcc/lsp_bridge.py` — безопасный async LSP JSON-RPC (argv-only, timeout, bounded, graceful).
- [ ] `bcc/features/code_intel.py` — регистрация LSP-capability в существующий REGISTRY
  (definition/references/hover/symbols/diagnostics), read-only (auto).
- [ ] `bcc/eval_scorecard.py` + endpoint — Bossman vs OpenCode benchmark aggregator.
- [ ] тесты (fake-LSP через argv; scorecard pure).
- [ ] регрессия + secret scan + push.

## Решения
- LSP/benchmark кладём в command-center (там tool loop, MCP, browser, registry).
- LSP-capability = read-only (auto): definition/references/hover/symbols/diagnostics.
- Ничего не дублируем: используем bcc.tools.REGISTRY, decide_effect, Feature.setup.

## Тесты (последнее известное)
- bossman-core: 906 passed / 4 skipped.
- command-center: 481 passed / 2 skipped (+ мои plugin-тесты).
- Известный флейк CI: `test_v21_failure_injection::test_state_survives_process_restart_midway`
  (179s teardown, SQLAlchemy/aiosqlite) — runner-only, НЕ мой код.

## Оставшиеся P0/P1
- нет открытых P0/P1 по моим изменениям.

## Следующий точный шаг
- Реализовать LSP bridge + code_intel feature + тесты (см. чеклист выше).

## UPDATE (LSP + benchmark done)
- DONE: `bcc/lsp_bridge.py`, `bcc/features/code_intel.py` (5 read-only caps, argv-only), `bcc/eval_scorecard.py`.
- Tests: `tests/test_code_intel_and_scorecard.py` — 13 passed (LSP over real pipes via fake server; scorecard summarize/compare).
- Features load: 30 total (code_intel + plugins present).
- Next: full command-center regression + secret scan + push; verify remote HEAD + CI.

## UPDATE (pack fully adapted → temp zip removed)
- Пакет BOSSMAN_NEXT_RUN_8_9_PACK адаптирован в репо: код (lsp_bridge/code_intel/eval_scorecard)
  + MD (MASTER_PROMPT, SCORECARD, LIVE_CONTEXT, EVENING_TEST_MATRIX). Временный ZIP удалён из git
  (по решению владельца — в репо только адаптированный код и отчёты, не временный пакет).
