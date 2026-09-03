# HANDOFF_STATE — K1MBA_TRADING_LEARNING_LAB

**START_REMOTE_SHA:** `44b8cd51efd894509912f4b80685031f213089c3`
(origin/claude/bossman-control-v03-43igbk на момент fetch)
**CURRENT_SHA:** `d2750fcef14d1ed818a2b9c31b93a787fb3e8db3` — мои изменения ещё не закоммичены, коммитит ведущий.

## Принятые решения

1. Модуль — отдельный подпакет `bossman-core/bossman/trading_learning/`; ленивые
   импорты и `__getattr__` по образцу `bossman.cost_control`.
2. Live-исполнение не реализовано вообще. Окружение может только ужесточить
   режим; `TRADING_EXECUTION=ON` в env игнорируется по построению.
3. Переиспользовано, а не продублировано:
   * `bossman.learning_guard.holdout.SecretHoldout` — режим SEALED_HOLDOUT;
   * `bossman.perimeter.require_scope` (SCOPE_CHAT/SCOPE_ADMIN) — авторизация ручек;
   * `bossman.approvals` — куда отправляется owner approval (ручка ingest отказная);
   * контракт `bcc.features.Feature` и дизайн-система `bx-*` — экран;
   * принцип «верификатор ≠ автор записи» взят из `learning/trace.py`.
   Второй Memory/Router/Benchmark/EventBus НЕ создавался.
4. Приём URL регистрируется, но не скачивается — класс BLOCKED, а не фикция.
5. Затравочный эпизод K1mba создаётся БЕЗ свечей: доказать им прибыль нельзя.

## Изменённые существующие файлы (только необходимое)

* `bossman-core/bossman/api.py` — одна строка `"bossman.trading_learning",` в
  кортеж `_include_stage_routers()`. Без неё модуль был бы DEAD_OR_UNWIRED.
* `command-center/ui/pages/index.js` — две строки (импорт страницы + её имя в
  `FEATURE_PAGES`), ровно как предписывает комментарий-контракт файла.

## Созданные продакшн-файлы

`bossman-core/bossman/trading_learning/`: `__init__.py`, `safety.py`, `models.py`,
`sanitize.py`, `ingest.py`, `adapters.py`, `frames.py`, `claims.py`, `strategy.py`,
`replay.py`, `verify.py`, `market.py`, `metrics.py`, `paper.py`, `backtest.py`,
`memory.py`, `lessons.py`, `seed.py`, `benchmark.py`, `telemetry.py`, `cli.py`,
`routes.py`.
`command-center/bcc/features/trading_lab.py`, `command-center/ui/pages/trading_lab.js`,
`docs/trading/K1MBA_TRADING_LEARNING_LAB.md`.

## Команды тестов

```
cd bossman-core && python3 -m pytest tests/test_trading_safety.py \
  tests/test_trading_lookahead.py tests/test_trading_claims.py \
  tests/test_trading_paper_memory.py tests/test_trading_pipeline_benchmark.py \
  tests/test_trading_wiring.py -q                       # 125 passed
cd bossman-core && python3 -m pytest tests -q            # 1809 passed, 30 skipped, 2 xfailed
cd command-center && python3 -m pytest tests -q          # 1022 passed, 3 skipped
python3 -m compileall -q bossman-core/bossman/trading_learning
python3 tools/ci_secret_scan.py                          # PASS
git diff --check                                         # чисто
python3 -m bossman.trading_learning.cli trading_benchmark   # exit 3 = NOT_READY (честно)
```

## Активные блокеры

* `ffmpeg` не установлен → `extract_audio` BLOCKED;
* локального ASR (whisper/faster-whisper/vosk) нет → `transcribe` BLOCKED;
* OCR-движка (tesseract/easyocr) нет → `chart_ocr` BLOCKED;
* нет одобренного владельцем READ_ONLY источника исторических рыночных данных →
  `verify_claims`/`run_backtest`/`paper_trade` из CLI отвечают BLOCKED;
* из-за трёх первых пунктов бенчмарк даёт `NOT_READY` — это верный ответ.

## Следующий шаг

1. Владелец решает, ставить ли ffmpeg + локальный ASR + OCR (новых зависимостей
   я не добавлял).
2. Подключить READ_ONLY-провайдер исторических свечей/CVD/OI/ликвидаций за
   явным approval — только после этого бэктест и paper-replay дают числа.
3. Дальше: реальный `verify_claims` по подключённым данным и первые эпизоды в
   EPISODIC_MEMORY; продвижение в PROCEDURAL_MEMORY только через гейт.
