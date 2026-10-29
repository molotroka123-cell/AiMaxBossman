# T4 — EVAL500 Baseline & Интеграция пакета `tools/eval500` (29.10.2026)

## 1. Проверка корпуса 500 сценариев (350 dev + 150 sealed holdout)
Команда:
```powershell
python tools/scenario_runner.py --validate-eval500
```
Результат:
- `EVAL500: dev 350 valid; holdout 150 sealed — file sha256 and 150 per-scenario hashes match the seal`
- `dev_sha256`: `d032d896815eb215206aa1fc3219a0dc6122cb117e5b687d4f6d9e0332b00a98`
- `holdout_file_sha256`: `3c526eb6117488a346fa05fe7057c5e3a38457abdc8707e8d8b7986264da6565`
- Holdout (`D:\Bossman\eval\heldout\eval500_holdout.json`) остаётся вне git и защищён `tools/eval500/holdout_guard.py`.

## 2. Перенос незакоммиченных инструментов из `wt-final-20261023` и защита роя (T5/T4)
В ветку перенесены и зафиксированы:
- `tools/eval500/run_judge_full.ps1`
- `tools/eval500/run_tournament.ps1`
- `tools/eval500/run_tournament_full.ps1`
- `tools/eval500/strata_packs.py`
- `tools/eval500/tournament_pdf.py`
- обновления `tools/eval500/runner.py` и `tools/eval500/tournament.py`
- В `tools/eval500/generate.py` и `tools/eval500/judge.py` прямые вызовы `swarm()._call(...)` заменены на `call_swarm_tracked(...)`, который проверяет `swarm().plan_models([model])`, прогоняет `ContextSanitizer` / `holdout_guard` и записывает использование лимитов бесплатных/платных моделей через `swarm().record_usage([res])`.

## 3. Квитанции прогонов (`receipt.json`, `failure_clusters.json`, `tournament-receipt.json`)
- **`receipt.json` + `failure_clusters.json`**: сгенерированы `tools/eval500/report.py baseline --run-id baseline-gemma41 --out docs/audits/2026-10-29-eval500-baseline`. Прогон `baseline-gemma41` (запущен `2026-10-25T08:52:25`) выполнил 19 сценариев и был поставлен на паузу файлом `D:\Bossman\eval\PAUSE` на время проведения модельного турнира (`481/500` сценариев в статусе `not_run` до снятия `D:\Bossman\eval\PAUSE` и выделения ~10–12 часов локального GPU).
- **`tournament-receipt.json`**: полный слепой турнир `tournament-full-20261026` (28 самых сложных сценариев по 2 на каждую из 14 категорий × 3 локальные модели = 84 прогона с оценкой судьёй):
  - Победы по категориям: **Gemma 4 31B — 6**, **Strata (Qwen3.8-Flash-Next IQ3_S) — 2**, **Qwen3.8 27B Q5 — 0**, ничьих — **6**.
  - Облачный расход: **`$0.00`**.
