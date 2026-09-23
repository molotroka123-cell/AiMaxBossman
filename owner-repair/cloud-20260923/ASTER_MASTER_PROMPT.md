# Aster — мастер-промпт: довести 1.1 + 1.2 до owner-run

Репозиторий `molotroka123-cell/AiMaxBossman`. **Работаешь и пушишь только в ветку
`claude/bossman-cloud-closure-owner-a6s1ki` (PR #74)** — это общая линия сведения 1.0+1.1+1.2.
Перед каждой работой: `git fetch --all --prune`, `git pull --no-rebase` этой ветки. Никаких
force-push, rebase чужих коммитов, новых final-веток. Пушить после КАЖДОГО законченного шага.
Claude продолжает параллельно в той же ветке — мелкие коммиты, merge (не rebase) при расхождении.

Прочитать сначала: `owner-repair/cloud-20260923/CHECKPOINT.md` (раздел «Не сделано»),
`CONVERGENCE_1_1.md`, `docs/terminal/TERMINAL_RUN_1_2_MASTER.md` (+ ACCEPTANCE_AND_OWNER_RUN.md),
`docs/evo/BOSSMAN_1_2_TERMINAL.md`, `docs/evolution/V1_1_FINAL_HANDOFF.md`, инструкцию владельца
в `release/bossman-owner` (Bossfield) и ветку `codex/bossfield-amd-30s` (037cd140).

## Задачи по приоритету
1. **CI зелёный на голове ветки.** root-ci, command-center-ci (включая core-runtime ubuntu+windows),
   bossman-core-ci, Windows bundle (coding path из архива, Owner-Run plan, terminal smoke).
   Красное → воспроизвести → падающий тест → фикс → зелёный. `measured intelligence retention`
   красный везде до замера на железе владельца — не «чинить» подгонкой.
2. **1.1 цикл — доделать поверхность** (движок уже есть: `bossman_v3/self_improvement/loop.py`,
   `verifier.py`, `attempts.py`):
   - `tools/bossman_evolve.py`: подкоманды `loop/status/pause/resume/stop/gate/soak` поверх
     `python -m bossman_v3.self_improvement.loop`; без путей из checkout; отгрузка в ZIP
     (`SUPPORT_SCRIPTS` + `config/evolution/*.json` в app-support).
   - `command-center/bcc/features/evolution.py`: `/api/evolution/status|start|pause|resume|stop|report`
     (start — дочерний процесс через `proc_tree`); терминал уже вызывает эти маршруты (`EvolutionAdapter`).
   - Telegram `/evolution_status|pause|resume|stop|report` в СУЩЕСТВУЮЩЕМ companion (без второго poller).
   - Запустить и влить черновик `owner-repair/cloud-20260923/wip/evolution_gate_UNTESTED.py.txt`
     как `gate.py` только после зелёных тестов; MOCK никогда не печатает AUTONOMOUS_SELF_IMPROVEMENT_READY.
   - `docs/evolution/EVOLUTION_LOOP.md` (русский).
3. **1.2 терминал — дожать:** prompt_toolkit==3.0.52 + wcwidth==0.8.4 в `tools/windows_bundle_lock.*`
   штатным инструментом с хэшами, затем в extra `runtime`; сквозные тесты `code`/`/diff`/`/memory`/
   `/skills`/`stop --all`/`resume`; экраны `run automation` (action queue) и `evolve --lab` (варианты)
   из реальных событий; parity matrix обновить.
4. **Модели:** три закреплённых профиля из `config/evolution/local-champions.json` в
   `tools/model_profiles.json` (один источник истины + тест синхронизации); порядок турнира и проверка
   88 GB в `model_fetch`; TTFT/prefill/RAM в `model_bakeoff`. Веса не качать.
5. **Навык** `huggingface-community-evals` (huggingface/skills@abc20ae5, sha256 a97f1c70…) в каталог
   как CANDIDATE; путь CANDIDATE→VERIFIED только по тестам каталога; навыки не дают прав.
6. **Bossfield** из `codex/bossfield-amd-30s` — по инструкции владельца в release, по смыслу, с тестами.
7. **Финал:** один SHA, весь CI зелёный, Windows ZIP (ID, размер, SHA-256), обновить CHECKPOINT и PR #74;
   слияние #74 → #73 → release только на зелёном SHA.

## Правила
- Один backend: без второй памяти, gateway, Telegram poller, coding engine.
- Windows: архив использует embeddable Python — PYTHONPATH/PYTHON* и cwd игнорируются; тесты
  запускать через `local_sidecar._runner_cmd`/`tool_run_tests`, проверять эмуляцией `python -I`.
- Не ослаблять/удалять тесты, не менять expected ради PASS; после изменений skips —
  `python tools/skips_registry.py` и `--check`; `git diff --check`.
- Никаких секретов в коде/логах; approvals никогда не автоматически; MOCK_MODEL подписан;
  OWNER_HARDWARE_CERTIFIED / 24H_SOAK_PASS из облака не заявлять.
- Отчёт: SHA, что сделано и чем проверено, что осталось.
