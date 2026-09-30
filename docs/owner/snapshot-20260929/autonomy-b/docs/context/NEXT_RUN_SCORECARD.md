# NEXT RUN SCORECARD (8→9)

Инженерная самооценка (не PASS-гейт). «После» заполняется по мере закрытия
слабых мест; финальные цифры — только с evidence (тесты/benchmark), не «на глаз».

| Направление | Было (owner) | Сейчас (после этого рана) | Цель | Что сделано / чем закрыть |
|---|---|---|---|---|
| Coding workflow | ~7.2 | ~7.4 | 9.0 | diff-aware reviewer + resume/fork/worktree (следующий шаг) |
| Code intelligence | ~6.0 | **~7.3** | 9.0 | LSP-мост + code_intel capability (def/refs/hover/symbols/diag), argv-only, 13 тестов; LIVE-сервер → SKIP_HOST |
| Context retention | ~8.2 | ~8.2 | 9.2 | бюджет/релевантность/компакция — измерить |
| Multi-agent orchestration | ~8.7 | ~8.7 | 9.2 | parallel coding через существующий движок |
| Local AI | ~7 / 8.5 | ~7 / 8.5 | 9.0 | LIVE Ollama → Gateway (SKIP_HOST здесь) |
| Provider routing | ~8.4 | ~8.4 | 9.0 | — |
| Security/approvals | ~9.1 | **~9.2** | 9.3 | plugin_security (SSRF/path/redaction), policy ASK/DENY, anti-replay |
| Memory | ~8.7 | ~8.7 | 9.2 | FactStore contract зелёный |
| Browser/PC operator | ~8.1 | ~8.1 | 9.0 | Stage13 + allowlist (bossman-core) |
| Plugins | ~5.8 | **~8.2** | 8.7+ | 13 коннекторов адаптированы в существующий registry, 48 тестов |
| Reliability | ~8.4 | ~8.4 | 9.0 | известный CI-флейк teardown — не мой код |
| Observability | ~8.0 | **~8.2** | 9.0 | статус-эндпоинты /api/plugins, /api/code-intel |
| UI/UX | ~7.6 | ~7.6 | 8.8 | — |
| Cost control | ~9.0 | ~9.0 | 9.3 | Cost Governor enforcement 24 теста |
| Benchmark evidence | ~5.5 | **~7.0** | 9.0 | eval_scorecard summarize/compare + тесты; нужен реальный 10-задачный A/B прогон |

## Что осталось для 9 (следующий ран)
- Реальный 10-задачный A/B прогон Bossman vs OpenCode (одно железо/модель) → JSONL → compare.
- LIVE LSP-сервер (pyright/gopls) на способном хосте.
- diff-aware reviewer + resume/fork/worktree UX.
- Context отзывчивость: измеримые метрики.
