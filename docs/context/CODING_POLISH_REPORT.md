# CODING WORKFLOW POLISH REPORT (POLISH Wave 3)

Принцип: НЕ второй session-engine. Расширяем существующие
tasks/runs/checkpoints/forks — `bcc/forks.py` остаётся авторитетом lineage
чекпоинтов. Здесь добавлена git-изоляция рабочей копии и diff-aware ревью.

## Канонический рабочий поток (реализовано на уровне command-center)
`TASK → CODING SESSION → ISOLATED WORKTREE → REPO/DIFF → LSP diagnostics →
EDIT → TESTS → ACTUAL DIFF → REVIEWER (diff-aware) → APPROVAL → MERGE/REJECT/ROLLBACK
→ DURABLE SUMMARY`.

## `bcc/coding_session.py` — `CodingWorktreeManager`
Всё через argv-only git (никакого shell), пути под confined-root.

| Операция | Гарантия |
|---|---|
| `create` | один branch+worktree на сессию; **база пинится к конкретному SHA** (не движущийся HEAD); исходный репозиторий воркеру READ-ONLY (git worktree add — отдельная копия); безопасное имя ветки/каталога; worktree в confined-root; двойная активная сессия запрещена |
| `status` | dirty/uncommitted + список изменённых файлов vs база |
| `diff` | настоящий `git diff` (stat + patch + files), bounded (`max_bytes`) |
| `merge_preview` | conflict-aware через `git merge-tree --write-tree` (без касания дерева) |
| `merge` | **сериализован** (`_MERGE_LOCK`); конфликт без `allow_conflicts` → блок; реальный merge в отдельном временном worktree — рабочее дерево источника цело |
| `discard` | явный (worktree remove + branch -D); **никакого auto-delete до merge** |
| `cleanup_orphans` | удаление осиротевших worktree-каталогов |
| durable-метаданные | JSON `sessions.json` в root — переживают рестарт; `get`/`list_sessions` восстанавливают состояние |

Знает про сессию: session_id, source_repo, base_ref(SHA), branch, worktree,
status(active/merged/discarded), created_at. Восстановление после рестарта — из
durable JSON. resume/fork lineage — через существующий `forks.py`
(fork_checkpoint / session_forks), не дублируется здесь.

## Diff-aware reviewer — `diff_aware_review()`
Ревью по НАСТОЯЩИМ артефактам (diff/тесты/диагностика), не по прозе агента.
Обязательный негативный инвариант (доказан тестами):
- claim DONE, а diff пуст → **reject** («нет доказательства изменений»);
- claim DONE, а тесты красные → **reject**;
- claim DONE без результата тестов → **reject**;
- LSP-диагностика > 0 ошибок → в findings;
- чувствительные пути (`.github/workflows`, `approvals`, `llm`, `auth`,
  `perimeter`, `secret`) → `requires_human=True`.
Вердикт `approved` только при: claim_done ∧ есть diff ∧ тесты зелёные ∧ нет
не-чувствительных findings. Evidence в ответе: число файлов, stat, tests_passed, diag.

## Code intelligence (LSP) — `bcc/lsp_bridge.py` + `bcc/features/code_intel.py`
Lightweight JSON-RPC мост НЕ выброшен — дополирован:
- capability negotiation: `initialize` сохраняет server capabilities;
  `supports(method)` проверяет провайдера ДО вызова (неизвестные caps →
  оптимистично, явно выключенные → `LSPError`);
- response normalization: `normalize_locations()` сводит Location/LocationLink/
  объект/список/None → `list[{uri, range}]`;
- сохранено: argv-only subprocess, workspace confinement, timeout, bounded
  payload (max_message_bytes), graceful shutdown.
Зарегистрированы read-only capability `code:definition/references/hover/symbols/
diagnostics` (`default_effect=auto`).

## Тесты
- `tests/test_polish_lsp_and_coding.py` — 15 passed: LSP capability record/reject/
  optimistic + normalization; worktree isolation (источник не тронут), real diff,
  clean+conflict merge preview, conflict blocked, discard+orphan cleanup,
  no-double-active; reviewer reject-empty/reject-red/approve-real/flag-sensitive.
- `tests/test_code_intel_and_scorecard.py` — 13 passed: fake-LSP над реальными
  pipes (Content-Length framing), argv-only AST-проверка, size bound, graceful
  shutdown, scorecard summarize/compare.

## Осталось на реальный хост (вечер)
- реальный pyright/gopls smoke (если хост поддерживает; иначе `SKIP_HOST`, не PASS);
- живой прогон полного цикла task→session→edit→tests→review→merge на реальной
  модели/репозитории — по `EVENING_LIVE_ACCEPTANCE.md`;
- A/B Bossman vs OpenCode (10 задач) — `eval_scorecard.py` готов агрегировать.
