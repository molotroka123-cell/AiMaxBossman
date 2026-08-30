# POLISH PHASE — REPORT (Wave 3 + LSP polish)

Дата: 2026-08-30 · Ветвь: `claude/bossman-control-v03-43igbk`
Метод: тройная сверка — текущий HEAD → зрелые GitHub-паттерны (04_REFERENCE_GITHUB_CROSSCHECK)
→ архитектурные инварианты Bossman. Никакого blind copy: адаптация под существующие интерфейсы.

## LSP polish (не выбрасываем lightweight мост — дополировали)
Референсы: lsp-client/lsp-client (async lifecycle, capability negotiation, typed surfaces),
python-lsp/python-lsp-jsonrpc (JSON-RPC framing). Решение — KEEP + polish:
- **capability negotiation**: `initialize` теперь сохраняет server capabilities;
  `supports(method)` проверяет провайдер ДО вызова; неизвестные caps → оптимистично,
  явно выключенные → `LSPError` (не дёргаем зря). Метод→ключ провайдера в `_CAP_KEY`.
- **response normalization**: `normalize_locations()` сводит Location / LocationLink /
  объект / список / None → единый `list[{uri, range}]`.
- Сохранено: argv-only, workspace confinement, timeout, bounded payload, graceful shutdown.
Файл: `command-center/bcc/lsp_bridge.py`.

## Coding worktree session (Wave 3) — расширяем forks, не строим второй engine
Референсы: opencode-worktree / opencode-agent / forge / agent-workspace (branch+worktree на
задачу, persistent id, conflict-aware merge, orphan cleanup, source read-only). Решение —
EXTEND существующих tasks/runs/checkpoints/forks (forks.py остаётся авторитетом lineage):
`command-center/bcc/coding_session.py` — `CodingWorktreeManager`:
- `create` — branch+worktree на сессию; **база пинится к конкретному SHA**; исходный
  репозиторий воркеру read-only (git worktree add — отдельная копия, рабочее дерево не трогаем);
  безопасное имя ветки/каталога, worktree в confined-root; двойная активная сессия запрещена.
- `status` — dirty/uncommitted + изменённые файлы vs база.
- `diff` — настоящий `git diff` (stat + patch + files), bounded.
- `merge_preview` — conflict-aware через `git merge-tree --write-tree` (без касания дерева).
- `merge` — сериализован (lock), конфликт без `allow_conflicts` → блок; реальный merge в
  отдельном временном worktree (рабочее дерево источника цело); статус → merged.
- `discard` — явный (worktree remove + branch -D), **никакого auto-delete до merge**.
- `cleanup_orphans` — удаление осиротевших worktree-каталогов.
- durable-метаданные (JSON в root) — переживают рестарт.
Всё через argv-only git (никакого shell).

## Diff-aware reviewer
`diff_aware_review()` — ревью по НАСТОЯЩИМ артефактам (diff/тесты/диагностика), не по прозе.
Негативный инвариант: claim DONE при пустом diff / красных тестах / без результата тестов →
reject. Чувствительные пути (approvals/llm/auth/perimeter/workflows/secret) → requires_human.

## Тесты
`command-center/tests/test_polish_lsp_and_coding.py` — 15 passed:
LSP capability record/reject/optimistic + normalization; worktree isolation (source untouched),
real diff, clean+conflict merge preview, conflict blocked, discard+orphan cleanup, no-double-active;
reviewer reject-empty/reject-red/approve-real/flag-sensitive.

Регрессия: command-center full **509 passed / 2 skipped** (было 494/2; +15, 0 регрессий);
secret scan PASS. bossman-core не затронут.

## Осталось по POLISH (следующие волны)
- Wave 2: живое исполнение внешних плагинов (нужны креды) — контракты/политика готовы.
- Wave 4/5: единый task→session→diff→review→approval→merge trace + Command Center UX.
- Wave 7: реальный 10-задачный A/B Bossman vs OpenCode (нужен хост/модель).
- Эти пункты требуют live-сервисов/хоста → по вечерней матрице `NEXT_RUN_EVENING_TEST_MATRIX.md`.
