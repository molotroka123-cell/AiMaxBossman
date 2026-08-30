# BOSSMAN — MASTER PROMPT NEXT RUN (8→9, beyond OpenCode)

Один промт на весь следующий ран. Работать строго по порядку. NO force push.
Каждые ~10 минут / после каждого крупного шага писать в
`docs/context/NEXT_RUN_LIVE_CONTEXT.md` (HEAD, что сделано, файлы, решения,
тесты, ошибки, оставшиеся P0/P1, следующий шаг) — чтобы при обрыве лимита
следующая модель продолжила без перечитывания всего репо.

## Правильная рамка
OpenCode — оболочка/исполнитель, не «умнее как модель». Bossman — control plane
(память + approvals + cost + внешние действия + PC control + long-running +
оркестрация). Не оценивать «на глаз» — мерить завершение задач A/B.

## Абсолютные правила (не нарушать)
Никакого второго: Gateway / approval / policy / event bus / secret store /
Telegram / browser runtime / Cost Governor / registry. LLM только через
существующий Gateway; Ollama cloud_policy=never; облако → Cost Governor → provider.
Никакого LLM→arbitrary shell. Unknown capability → DENY. External write → ASK.
SKIP не превращать в PASS; mock не является evidence для live-гейта.

## Порядок
1. FETCH → зафиксировать START_HEAD (реальный remote), прочитать
   `docs/context/NEXT_RUN_LIVE_CONTEXT.md`, `NEXT_RUN_SCORECARD.md`.
2. AUDIT/SCORE BEFORE — заполнить «сейчас» по 15 направлениям.
3. CODING PARITY — diff-aware reviewer поверх существующего dev_factory/reviewer;
   resume/fork/worktree поверх существующих snapshots/forks; parallel coding —
   через существующий движок задач, без второго оркестратора.
4. LSP — уже есть база: `bcc/lsp_bridge.py` + `bcc/features/code_intel.py`
   (definition/references/hover/symbols/diagnostics, read-only, argv-only).
   Доработать: конфиг `LSP_SERVERS` (JSON lang→argv), кэш клиента на воркспейс,
   реальный языковой сервер в LIVE (pyright/gopls) → SKIP_HOST если нет.
5. CONTEXT — отзывчивость: бюджет контекста, релевантность, компакция; измерить.
6. PLUGINS — уже интегрированы (`bcc/features/plugins.py`, 13 коннекторов,
   `bcc/plugin_security.py`). Довести живые адаптеры под доступные креды.
7. SECURITY — red-team по SSRF/redirect/SQL/MCP/path/creds/approval-replay.
8. UX/OBSERVABILITY — статус-эндпоинты (`/api/plugins`, `/api/code-intel`,
   `/api/benchmarks`), метрики, аккуратные ошибки.
9. OPENCODE BENCHMARK — 10 одинаковых coding-задач Bossman vs OpenCode на одном
   железе/модели → JSONL → `bcc/eval_scorecard.py compare` → таблица (success,
   tests-green, interventions, cost, time, security-violations, resume-after-restart).
10. FULL REGRESSION — bossman-core + command-center + secret scan; точные счётчики.
11. SCORE AFTER + EVENING READY — по `NEXT_RUN_SCORECARD.md`.
12. PUSH — ff, verify remote HEAD, проверить CI на финальном SHA
    (известный флейк `test_v21_failure_injection` teardown — не блокер, не мой код).

## Уже сделано (в remote, продолжать отсюда)
- Plugins: `aa7f1a0` (адаптеры 13 коннекторов + security).
- Code intelligence: `bcc/lsp_bridge.py`, `bcc/features/code_intel.py` (+13 тестов).
- Benchmark evidence: `bcc/eval_scorecard.py` (summarize/compare + тесты).
- Pythia-фиксы, RC-gate отчёты — зелёные.

## Вечерний прогон (не писать новый код)
По `04_EVENING_LIVE_TEST_MATRIX.md`: Ollama, Windows, Notepad, память, плагины,
approvals, browser, Cost Governor, Pythia, chaos, security, полный regression,
финальный SHA. LIVE без host/creds → честный SKIP, не PASS.
