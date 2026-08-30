---
lane: 2
subsystem: coding-lsp-benchmark
base_head: 3c152d1
branch: claude/audit-lane2-coding
final_head: 9bfaf744827d0e814331f8cd3a1ffdce7be64931
verdict: PASS
p0: 0   p1: 0   p2: 3
tests: {passed: 28, failed: 0, skipped: 0}
skip_labels: [SKIP_HOST, NOT_TESTED_LIVE]
host_only_remaining: "real pyright/gopls definition/hover/diagnostics (requires LSP_SERVERS + binaries); concurrent merge contention under load; 10-task Bossman vs OpenCode benchmark on single hardware/model/repo"
---

# AUDIT LANE 2 — CODING SESSION / LSP / EVAL SCORECARD / BENCHLAB

**Branch:** `claude/audit-lane2-coding` from `3c152d1` (CI green)  
**Date:** 2026-08-30  
**Scope:** `coding_session.py`, `lsp_bridge.py`, `features/code_intel.py`, `eval_scorecard.py`, `features/benchlab.py`, `features/opencode.py`, `features/review_gate.py`, tests `test_polish_lsp_and_coding.py` + `test_code_intel_and_scorecard.py`  
**Invariants:** no second Gateway/Registry/Policy/Approval engine; canonical cycle preserved; `forks.py` is authority for checkpoint lineage (read-only).

---

## 1. Truth-Matrix / Чеклист

| # | Пункт | Статус | Доказательство |
|---|-------|--------|----------------|
| 1 | Worktree confinement под root | PASS | `safe_name` strips `/\:` → `-`, trunc 64, rejects empty; `wt=(root/sid).resolve(); if root not in wt.parents → reject` (`coding_session.py:111-114`). Live: `safe_name("../evil")=="evil"`, `safe_name("/etc/passwd")=="etc-passwd"`, traversal blocked (repro). |
| 1 | База пинится к SHA (не движущийся HEAD) | PASS | `rev-parse base_ref → base_sha` перед `worktree add` (`coding_session.py:118-122`). Diff/preview use `base_sha` (`diff:137`, `merge_preview:153`). Repro: `base_ref` becomes SHA `d7a6ed...` in durable JSON. |
| 1 | Исходный чекаут read-only | PASS | `git worktree add -b branch wt base_sha` creates separate copy; test `test_create_isolated_worktree_leaves_source_untouched` — write in wt does not affect src `a.py`. |
| 1 | Запрет двойной активной сессии | PASS | `if sid in data and status=="active" → CodingSessionError` (`coding_session.py:106`). Repro PASS, `test_no_double_active_session`. Reuse after `discarded` allowed (correct). |
| 1 | Безопасные имена | PASS | `safe_name` via `_SAFE=[^a-zA-Z0-9._-]+`, `strip("-.")`, `[:64]` (`coding_session.py:36-42`). Edge: `../etc`→`etc`, `a/b`→`a-b`, `.hidden`→`hidden`, 200-char truncated. |
| 1 | Orphan-cleanup | PASS | `cleanup_orphans` removes directories not in `active` set, skips `.*` (`coding_session.py:205-214`). Repro + test `test_discard_and_orphan_cleanup`. |
| 1 | Нет удаления worktree до merge/явного discard | PASS | No `auto` delete; only `discard()` removes wt+branch explicitly (`coding_session.py:194-203`). Comment: `Не авто, только по команде`. `merge()` does not delete wt (only temp merge wt). |
| 2 | Merge сериализация (lock) | PASS | `_MERGE_LOCK = asyncio.Lock()` (`29`), `async with _MERGE_LOCK` in `merge` (`171`). Prevents concurrent merges. |
| 2 | Conflict-aware preview (merge-tree) | PASS | `merge_preview` uses `git merge-tree --write-tree target branch` (`155-156`), non-zero → `clean=False`, collects conflicts (`159-164`). Real merge `check=False` + abort on failure (`181-183`). |
| 2 | Конфликт → STOP/блок, а не угадывание | PASS | `preview["clean"]==False and not allow_conflicts → {"merged":False, "reason":"conflicts"}` (`173-174`). Test `test_merge_conflict_is_blocked` PASS (both sides edit `a.py` differently → preview clean=False, merge blocked). |
| 2 | Реальный merge не портит источник | PASS | Merge via detached temp worktree on `into` (`179`), `merge --no-edit` there, `merge --abort` on failure, `worktree remove --force` in `finally` (`187`). Source working tree untouched. |
| 3 | Durable JSON `sessions.json` (atomic) | PASS | `_save` via `.tmp` + `replace` (`88-92`), `_load` handles `ValueError/OSError → {}` (`85-86`). Corrupt file returns `{}` (repro). |
| 3 | get/list восстанавливают состояние | PASS | `get` reads file each time (`93-95`), `list_sessions` (`97-98`). Restart test: new `CodingWorktreeManager(same_root)` returns same meta, `list==1`. |
| 3 | Merge не запускается повторно после рестарта | PASS | No auto-merge on load; `status` stays `active` after restart (repro: `status active`); only explicit `merge()` mutates to `merged`. |
| 3 | Тест «рестарт в середине» | PASS (добавлен в отчёт) | Live repro above: create session → write dirty file → instantiate new Manager → `get`/`diff` still works, `base_ref` pinned SHA, `status active`, no auto merge. `test_no_double_active_session` covers part. |
| 4 | Diff-aware reviewer: DONE при пустом diff → REJECT | PASS | `diff_aware_review` check `claim_done and not diff_files → finding` (`236-237`), `approved = claim_done and bool(diff_files) and tests_passed is True and not non-sensitive-findings` (`247`). Repro PASS. |
| 4 | DONE при красных тестах → REJECT | PASS | `tests_passed is False → finding "тесты красные"` (`238-239`). Repro PASS `test_reviewer_rejects_done_with_red_tests`. |
| 4 | DONE без результата тестов → REJECT | PASS | `tests_passed is None → finding "без результата"` (`240-241`). Repro PASS. |
| 4 | Чувствительные пути → requires_human | PASS | `touched_sensitive = {p for f in diff_files for p in sensitive_paths if p in f}` (`244`), `requires_human=bool(...)` (`250`). Default tuple includes `.github/workflows`, `bossman/approvals`, `bossman/llm`, `secret`, `auth`, `perimeter`. Test `test_reviewer_flags_sensitive_paths_for_human` PASS. |
| 4 | Инвариант интегрально `approved` | PASS | `approved` false if any non-sensitive finding; diagnostics_errors also blocks approval. Verified with `test_reviewer_approves_with_real_evidence` (only when all evidence present → True). |
| 5 | LSP capability negotiation (выкл → отказ) | PASS | `LSPClient.supports` checks `_CAP_KEY` against `capabilities` (`78-89`); if `capabilities=={} → True` (optimistic), else `bool(capabilities.get(key))`. `_require` raises `LSPError` if not supported (`155-157`). Test `test_lsp_rejects_unsupported_capability` (symbols_only caps: symbols PASS, definition REJECT via LSPError). |
| 5 | Неизвестный → оптимистично | PASS | `if not capabilities → return True` (`84-85`), test `test_lsp_empty_caps_is_optimistic`. Fake-LSP with `CAPS=none → {}` → `supports()==True`. |
| 5 | Location/LocationLink normalization | PASS | `normalize_locations` handles `None→[]`, `dict→[dict]`, `LocationLink targetUri/targetSelectionRange||targetRange`, `Location uri/range`, filters junk (`92-108`). Test `test_normalize_location_and_locationlink` + `test_lsp_initialize_symbols_definition`. |
| 5 | argv-only | PASS | `create_subprocess_exec(*argv)` (`62-63`), no `shell=True`, no `create_subprocess_shell` (grep: only comment line). Verified via `test_lsp_uses_argv_not_shell` AST check. |
| 5 | Timeout | PASS | `LSPConfig.timeout_s=8.0` (`31`), `wait_for(fut, timeout_s)` (`145`). `_git` timeout 60s (`56`). |
| 5 | Bounded payload | PASS | `max_message_bytes=4MiB` (`32`), `_send` checks `len(body)>max → raise` (`200-201`), `_read_message` checks `length>max or <0 → raise` (`245`), header line `>8192 → raise` (`238`). Test `test_lsp_message_size_bounded`. |
| 5 | Graceful shutdown | PASS | `close()` → `shutdown→exit→wait 2s→terminate→wait 2s→kill` (`111-133`), `_reader.cancel()` (`132`). Test asserts `proc.returncode is not None` after close. |
| 5 | Fake-LSP детерминирован | PASS | Two fake servers (textwrap in tests) with `Content-Length` framing, deterministic `initialize→capabilities`, `symbols→[{name:main}]`, `definition→{uri,range}`. |
| 5 | Real pyright/gopls | SKIP_HOST | Host check `which pyright / gopls` → `NOT FOUND` (Windows runner). Correctly marked `SKIP_HOST`, not fake PASS. Code documents `if host есть, иначе SKIP_HOST`. |
| 6 | summarize/compare корректны | PASS | `summarize` groups by `executor`, `success_rate = sum(bool(success))/n`, `avg_*` via `fmean` (`35-48`); `compare` needs both `a,b` else `insufficient data` (`55-57`), `wins` 5 criteria. Tests `test_summarize_metrics`, `test_compare_verdict`, `test_compare_insufficient_data` PASS. |
| 6 | НЕ заявляй Bossman>OpenCode без 10 задач | PASS / NOT_TESTED | `eval_scorecard.py` never claims superiority; `compare` returns raw `a_wins/criteria`, caller must interpret. Report states `NOT_TESTED_LIVE` for claim (no 10-task run on single machine/model/repo exists). No hardcoded superiority string in code. |

---

## 2. Находки

### P0 — нет

Критичных нарушений инвариантов, RCE, обхода approve, утечки секретов, второго engine не найдено. Канонический цикл `TASK→SESSION→WORKTREE→DIFF→LSP→TESTS→REVIEWER→APPROVAL→MERGE` сохранён; `forks.py` остаётся авторитетом lineage.

### P1 — нет

Требующих немедленного фикса багов с exploitable impact не найдено. Все checklist-инварианты имеют тест-доказательство (или честный SKIP_HOST).

### P2 — 3 (низкий риск, не блокируют merge)

| # | Зона | Описание | Воспроизведение | Рекомендация | Статус |
|---|------|----------|-----------------|--------------|--------|
| P2-1 | `lsp_bridge` / `code_intel` | `code_intel._run` резолвит `workspace` через `Path.resolve(strict=True)` и проверяет `is_dir`, но не ограничивает его `allowed_roots` (в отличие от `tools_code.resolve_root`). Теоретически агент может запросить LSP на `/etc`/`C:\Windows` если `LSP_SERVERS` настроен. LSP read-only, но расширение поверхности чтения нежелательно. | `monkeypatch.setenv LSP_SERVERS='{"python":["python","-m","pylsp"]}'` + вызов `code:definition` с `workspace=/etc` и `uri=file:///etc/passwd` → сервер стартует с `cwd=/etc` (проверено чтением кода). | Добавить `if not _within(workspace, allowed_roots)` перед `LSPClient` (переиспользовать `allowed_roots` из `tools_code` или `config.ROOT`). Diff ~5 строк, без нового engine. | Зафиксировано, не чиню в этом lane (требует импорта allowed roots, риск цикла). Оставлено как находка. |
| P2-2 | `eval_scorecard.load_jsonl` | Читает весь файл в память (`Path.read_text().splitlines()`). При `runs.jsonl` в сотни МБ (теоретически) возможен OOM. Benchmark на одной машине ограничен 10 задачами по чеклисту, практически <1 МБ, но без стриминга. | `load_jsonl` на синтетическом 200 МБ файле (не создавался, code review). | Заменить на построчное `open().readline` / `mmap`. | Находка, не чиню (не P0, вне критического пути). |
| P2-3 | `coding_session._git` | `git rev-parse base_ref` где `base_ref` приходит из внешнего `create(..., base_ref="HEAD")`. Передаётся через `*args` в `create_subprocess_exec` (argv-only, без shell), инъекция невозможна, но несуществующий ref даёт `CodingSessionError` с сообщением git. Нет валидации `base_ref` формата. | `await mgr.create("s", src, base_ref="not-a-ref")` → `git rev-parse failed: fatal:...` (ожидаемо). | Опционально валидировать `base_ref` regex `^[a-zA-Z0-9/_\-.]+$` до вызова git. | Находка, не чиню (argv-only уже безопасно, fail-closed). |

### Заметки (не P-уровень, для контекста)

- `coding_session.status` возвращает `changed_vs_base` как union `diff --name-only` (committed) + `porcelain` (uncommitted) — корректно покрывает dirty и committed diff.
- `merge_preview` формирует `conflicts` best-effort из `out+err` (ищет `CONFLICT`, `.py`, `/`) — достаточно для UI блокировки, но не для точного списка файлов (приемлемо, т.к. `merge` с `abort` — источник истины).
- `lsp_bridge._read_message` ограничивает заголовок `8192` байт и `Content-Length` > `max_message_bytes` — защита от OOM.
- `benchlab._measure` честно помечает `tool_calling: "not_tested"` вместо хардкода.

---

## 3. Тесты

### 3.1 Точечные (требование задачи)

```
cd command-center && python -m pytest tests/test_polish_lsp_and_coding.py tests/test_code_intel_and_scorecard.py -q
```

**Результат:** `28 passed in 2.59s` (детально `pytest -v` 28/28 PASS, 0.00 FAIL).

Разбивка:

- `test_polish_lsp_and_coding.py` (15): `lsp_records_capabilities`, `lsp_rejects_unsupported`, `lsp_empty_caps_is_optimistic`, `normalize_location_and_locationlink`, `safe_name`, `create_isolated_worktree_leaves_source_untouched`, `diff_reports_real_patch`, `merge_clean_and_conflict_preview`, `merge_conflict_is_blocked`, `discard_and_orphan_cleanup`, `no_double_active_session`, `reviewer_rejects_done_with_empty_diff`, `reviewer_rejects_done_with_red_tests`, `reviewer_approves_with_real_evidence`, `reviewer_flags_sensitive_paths`.
- `test_code_intel_and_scorecard.py` (13): `lsp_initialize_symbols_definition_over_real_pipes`, `lsp_empty_argv_rejected`, `lsp_negative_position_rejected`, `lsp_message_size_bounded`, `lsp_uses_argv_not_shell`, `code_intel_registers_readonly_caps`, `code_intel_no_server_is_graceful`, `code_intel_status_endpoint`, `summarize_metrics`, `compare_verdict`, `compare_insufficient_data`, `load_jsonl_rejects_non_object`, `load_jsonl_roundtrip`.

### 3.2 Дополнительные ручные прогоны (red-team / durability)

- Confinement: `safe_name("../evil")=="evil"`, `safe_name("/etc/passwd")=="etc-passwd"` → traversal blocked; `root in wt.parents` check PASS.
- Base pinning: `base_ref` SHA persisted (`d7a6ed...`), `diff` after restart returns `['a.py']`.
- Double session: second `create("s6")` → `CodingSessionError` PASS.
- Orphan cleanup: stray dir removed PASS.
- Restart-in-middle: `CodingWorktreeManager(tmp)` → `get`/`list`/`diff` after new instance PASS, status stays `active`, no auto-merge.
- LSP real host: `which pyright/gopls` → NOT FOUND → `SKIP_HOST` (честно, не PASS).
- `diff_aware_review` negative invariants: empty diff → REJECT, red tests → REJECT, `None` → REJECT, good → APPROVE, sensitive → `requires_human`.
- Eval: `compare([only bossman]) → verdict="insufficient data"` (honest), no hardcoded `Bossman>OpenCode`.

### 3.3 Счётчики

| Набор | PASS | FAIL | SKIP | Метка |
|-------|------|------|------|-------|
| `test_polish_lsp_and_coding.py` + `test_code_intel_and_scorecard.py` (требование) | **28** | **0** | **0** | |
| Ручные live-пробы (worktree/LSP/diff/reviewer) | **12** | **0** | **1** | `SKIP_HOST` (real pyright/gopls not installed on Windows runner) |
| Benchmark `Bossman>OpenCode` 10-task claim | — | — | **NOT_TESTED_LIVE** | Нет 10 задач на одной машине/модели/репо; `eval_scorecard` не заявляет победу, `compare` returns `insufficient data` when <2 executors. |

**Итого по требованию:** `PASS 28 / FAIL 0 / SKIP 0` (строго, без fake-PASS). С учётом доп. проб: `PASS 40 / FAIL 0 / SKIP 1 (SKIP_HOST) + 1 (NOT_TESTED_LIVE)`.

### 3.4 Что осталось только на реальном хосте

- Real `pyright` / `gopls` against actual project (definition/hover latency, diagnostics push) — `SKIP_HOST` (Windows dev machine without server binaries; requires `pip install pyright` or `go install gopls` + `LSP_SERVERS` env).
- Git `merge` under load (concurrent merges via `_MERGE_LOCK` under real contention) — логика lock проверена статически + unit, но не под 10 параллельными merges.
- Benchmark 10-task `Bossman vs OpenCode` on single hardware/model/repo — `NOT_TESTED_LIVE` (requires real OpenCode `serve` + model budget).

---

## 4. Соответствие инвариантам архитектуры

- **No second engine:** `coding_session.py` дополняет `features/forks.py` (checkpoint lineage), не дублирует; `code_intel.py` регистрирует в `tools.REGISTRY` (`setup` → `REGISTRY.register`), не создаёт новый; `benchlab`/`eval_scorecard` — чистые функции/хранилище `benchmarks` таблицы, не второй Cost/Session engine; `opencode` использует `bridge_for`/`approved_dir` (единый perimeter), `review_gate` — хук `engine.gate_completion`.
- **Canonical cycle:** `intent → worktree (typed session) → scopes` (confinement+safe_name) → `approval` (reviewer `requires_human` for sensitive) → `executor` (git argv-only) → `fresh result` (diff/status) → `audit` (diff_aware_review evidence) соблюдён.
- **Secrets:** no secret in logs/files; `LSP`/`git` args are paths/refs, not creds.
- **Pythia:** не затрагивается (read-only в других лейнах).

---

## 5. Вывод

Цикл `TASK→SESSION→WORKTREE→DIFF→LSP→TESTS→REVIEWER→APPROVAL→MERGE` доказан тестами (28/28) и ручными репродукциями. Нарушений P0/P1 нет. Три P2 (LSP workspace confinement, load_jsonl streaming, base_ref validation) — низкий риск, оставлены как находки для следующего цикла. Real-language-server и 10-task benchmark честно помечены `SKIP_HOST`/`NOT_TESTED_LIVE`.

**Рекомендация:** lane готов к merge; общие файлы не трогались.


