# Ночная сборка 05→06.10.2026 — журнал чекпоинтов

Ветка: `goal/bossman-self-improvement-tree-20261005`. Базовый SHA: `2a69b34b`.
Same-product Terminal Run contract: пульт, CLI и дашборд — один и тот же backend; этот журнал ничего не вводит отдельно.
Лестница North Star: достигнут только уровень `SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT`; `SELF_REPAIR_SINGLE_CYCLE_PASS`
в эту ночь **не заявляется**, пока в журнале нет строки с независимой проверкой (см. задачу 5).

Правило: PASS пишется только со ссылкой на лог/CI по точному SHA. Всё остальное — «частично» или «не проверено».

## Чекпоинт 1 — CI по HEAD (задача 1)

Состояние на `2a69b34b` (GitHub Actions, ветка):
- root-ci (run 37383214099): **FAIL** — `3 failed, 2935 passed, 47 skipped` на py3.11 и py3.12. Тот же провал на `6a5e3d26`,
  `98e6b073`, `469e51e8`, `5d888ce9` → красный не от последних правок, а накопленный.
- Solana safety gates, PostgreSQL run contracts, ASTRA acceptance: success. Command Center CI и Bossman Core CI на этом SHA
  были `pending`/`in_progress` в момент проверки (на предыдущих SHA — `cancelled` из-за гонки concurrency, не FAIL).

Первопричины трёх падений root-ci (воспроизведены локально, Linux, py3.11):
1. `tests/test_ci_secret_scan.py::test_repository_itself_is_clean` — две независимые причины:
   a. `command-center/ui/tests/technical_log.test.mjs` содержит синтетические «ключи»-муляжи для проверки редактирования
      (`sk-fakecredential…`, `AKIA000…`, jwt `…fakeSignature`) без штатной пометки `ci-secret-scan: allow`.
      Фикс: пометка на 4 строках, ровно по штатному механизму (значения заведомо фальшивые). Node-тест файла: 15/15.
   b. Два журнала `docs/testing/sessions/2026-10-02_*.jsonl` по ~5 МБ превышали `MAX_BYTES` (2 МБ) и отбраковывались как
      «unscannable oversized file». Фикс в `tools/ci_secret_scan.py`: крупные **текстовые** журналы (.jsonl/.log/.json/.md/…, до 64 МБ)
      сканируются построчно паттернами провайдеров; не-текст и сверх лимита — по-прежнему fail-closed. Это ужесточение покрытия,
      а не ослабление. В обоих журналах 0 находок.
      Тест `test_oversized_text_log_is_scanned_by_stream_and_still_fails_closed_otherwise` (чистый крупный лог проходит;
      крупный лог с секретом ловится; крупный не-текст отвергается) — **падает на старом сканере**, проходит на новом.
2. `docs/testing/SKIPS_REGISTRY.md` устарел (`skips_registry.py --check`): добавлен новый skip в
   `test_chat_technical_log_browser.py`, сдвинулись номера строк. Фикс: регенерация штатным `tools/skips_registry.py`
   (373 записи, без пустых причин).
3. `tests/test_exact_sha_certify.py::test_owner_release_scenario_79_…` — зависимость от порядка файлов: тест импортировал
   `scn_18_recovery_release` под обычным именем, а `scenario_runner.discover()` — под `bossman_owner_scn_scn_18_…`;
   декоратор `@scenario` регистрирует id один раз → `ValueError: сценарий OS-76 уже реализован`. Воспроизведено парой
   `pytest tests/owner_scenarios tests/test_exact_sha_certify.py` (1 failed) и в обратном порядке (1 failed + 8 errors).
   Фикс: тест грузит модуль под тем же именем, что и `discover()`. После фикса оба порядка: 117 passed.

Локальная проверка после фиксов (не заменяет CI): `pytest tests` — см. ниже строку «итог корневого набора»;
`skips_registry --check` PASS; `update_readme_scorecard --check` PASS; `compileall` OK; `git diff --check` OK;
`ci_secret_scan` PASS.

Итог корневого набора (локально, Linux py3.11, после фиксов): `2939 passed, 47 skipped, 0 failed`. CI по новому SHA — ниже, когда завершится.
