# K1MBA_TRADING_LEARNING_LAB

Модуль обучения на материалах трейдера K1mba с обязательной проверкой данными.

**Замысел.** K1mba — источник учебного материала и гипотез, НЕ оракул. Модуль
превращает материал в типизированные claim'ы, а затем сам проверяет, где логика
работает статистически, а где это красивое объяснение постфактум.

## Режим безопасности (не обсуждается)

| Параметр | Значение | Где |
|---|---|---|
| `TRADING_EXECUTION` | `OFF` | `bossman/trading_learning/safety.py` |
| `PAPER_TRADING_ONLY` | `true` | там же |
| `OWNER_APPROVAL_REQUIRED` | `true` | там же |
| `EXTERNAL_WRITE_ACTIONS` | `DENY` | там же |

Разрешённый путь: `historical_analysis → replay → simulation → paper_trading → report`.

Live-исполнения не существует физически: в пакете нет ни клиента биржи, ни
HTTP-вызовов наружу — это проверяется тестом
`test_trading_safety.py::test_module_exposes_no_live_client`.
Переменная окружения `TRADING_EXECUTION=ON` НЕ включает торговлю: `execution_mode()`
всегда возвращает `OFF`, а попытка видна в статусе как `env_requested_live_ignored`.

Одобрение владельца обязано принадлежать человеку. Идентичность, похожая на
агента или модель (`self`, `bossman-agent`, `claude-*`, `gpt-*`, `assistant`,
`auto*`), отвергается — approval, выданный моделью самой себе, не является approval.

## Что реально доступно в этом окружении

| Шаг | Технология | Статус | Класс |
|---|---|---|---|
| `ingest_video` (файл) | stdlib sha256 | OK | REAL_SANDBOX |
| `ingest_video` (URL) | загрузчика нет | BLOCKED | BLOCKED |
| `extract_audio` | ffmpeg | **BLOCKED** — бинаря нет | BLOCKED |
| `transcribe` | whisper/vosk/… | **BLOCKED** — движка нет | BLOCKED |
| `extract_frames` | OpenCV 5.0.0 (FFMPEG backend) | OK | REAL_SANDBOX |
| `chart_ocr` | tesseract/easyocr | **BLOCKED** — движка нет | BLOCKED |
| `extract_claims` | детерминированные правила | OK | REAL_SANDBOX |
| `normalize_strategy` | — | OK | REAL_SANDBOX |
| `verify_claims` | независимый верификатор | OK | HISTORICAL_REPLAY |
| `compile_backtest` / `run_backtest` | — | OK | HISTORICAL_REPLAY |
| `paper_trade` | бумажный брокер в памяти | OK | SIMULATED |
| `lesson_builder` | — | OK | SIMULATED |
| `trading_benchmark` | 4 режима | OK | HISTORICAL_REPLAY |

Статусы считаются вызовом `adapters.probe_all()`, а не константой: появится
ffmpeg — статус изменится сам.

## Типы знания

`AUTHOR_CLAIM`, `MARKET_OBSERVATION`, `HYPOTHESIS`, `ENTRY_CONDITION`,
`EXIT_CONDITION`, `INVALIDATION`, `RISK_RULE`, `POSITION_MANAGEMENT`,
`EXPECTED_OUTCOME`, `RETROSPECTIVE_COMMENTARY`.

Claim не существует без происхождения: конструктор отвергает запись без
`source_id`, `video_hash`, `asset`, `venue`, `timeframe`, `raw_quote_or_frame_ref`,
`extraction_model` и timezone-aware `created_at`/`collected_at`. Наблюдение с
меткой из будущего не создаётся вовсе.

Статусы проверки: `UNVERIFIED`, `PARTIALLY_SUPPORTED`, `DATA_SUPPORTED`,
`DATA_CONTRADICTED`, `UNVERIFIABLE`, `QUARANTINED`.

**AUTHOR_CLAIM никогда не попадает в процедурную память** — это проверяется
`test_trading_paper_memory.py::test_author_opinion_can_never_be_promoted`.

## Анти-lookahead

Фазы `T0 / T1 / T2 / T3`. В `T1` недоступны: свечи, закрывшиеся позже момента
решения; claim'ы, собранные позже решения; типы `EXPECTED_OUTCOME` и
`RETROSPECTIVE_COMMENTARY`; протухшие наблюдения (> 30 дней); карантин.
Единственный законный вход модели — `replay.build_decision_context()`.
Смешение активов, площадок и таймфреймов — `ContextMismatch`.

## Режимы рынка

`PRICE_UP_CVD_UP_OI_UP`, `PRICE_UP_CVD_WEAK_OI_UP`, `PRICE_DOWN_OI_DOWN`,
`PRICE_DOWN_OI_UP`, `SHORT_SQUEEZE`, `LONG_SQUEEZE`, `CONTINUATION`,
`FAILED_BREAKOUT` (отдельная функция `market.failed_breakout`), `RANGE`, `UNKNOWN`.

## Математика качества

`EV = win_rate × avg_win − loss_rate × avg_loss − fees − funding − slippage − execution_error`.
Плюс expectancy в R, profit factor, максимальная просадка, ECE-калибровка, доля
ложных срабатываний, доля упущенных возможностей, дисциплина стопа, результат
вне выборки, покрытие режимов, чувствительность к расходам, доверительный
интервал Уилсона.

«Прибыльная стратегия» не заявляется, пока не выполнены ВСЕ гейты:
выборка ≥ 30, out-of-sample ≥ 10, EV > 0 после расходов, out-of-sample EV > 0,
≥ 2 режима, нижняя граница CI win rate > 0. Иначе вердикт
`INSUFFICIENT_EVIDENCE`, а решение — `NO_TRADE`/`INSUFFICIENT_EVIDENCE`.

## Память

`WORKING_STATE` / `EPISODIC_MEMORY` / `PROCEDURAL_MEMORY` / `QUARANTINE`.
Прямой записи в процедурную память нет: всё новое попадает в карантин, а
`TradingMemory.promote()` требует одновременно: ≥ 3 независимых эпизодов,
чистоту lookahead, положительный EV вне выборки на достаточной выборке,
верификатора ≠ экстрактора, непустую цепочку происхождения и отсутствие
противоречий. Любой отказ пишет причину обратно в карантин.

## Затравочный эпизод

`seed.py` — разбор K1mba строго с меткой `SCREENSHOT_OBSERVED`. Эпизод создан
БЕЗ свечей: прогнать его в бэктесте и «доказать прибыль» физически невозможно.
`LIVE_PROVEN` для него запрещён (`assert_live_proof` требует биржу, размер,
плечо, фактические филлы, комиссии, funding, реализованный P&L и сырые данные).

## Бенчмарк

`DEVELOPMENT`, `SEALED_HOLDOUT` (переиспользует `bossman.learning_guard.holdout.SecretHoldout`),
`ADVERSARIAL`, `PAPER_REPLAY`. Вердикт `READY` выдаётся только когда каждая
строка прошла И имеет класс доказательности не ниже HISTORICAL_REPLAY И ни одна
возможность пайплайна не BLOCKED. В текущем окружении вердикт — `NOT_READY`
из-за отсутствия ffmpeg/ASR/OCR, и это честный ответ, а не ошибка.

## Точки вызова

* ядро: `bossman/api.py` → `_include_stage_routers()` → `bossman.trading_learning.router`
  (`/trading-lab/status|capabilities|seed|benchmark|ingest`);
* Command Center: `bcc/features/trading_lab.py` (авто-подхват `load_features()`)
  → `/api/trading-lab/status|seed|benchmark|memory`;
* экран: `command-center/ui/pages/trading_lab.js`, зарегистрирован в `ui/pages/index.js`;
* CLI: `python -m bossman.trading_learning.cli <команда>`; код выхода 3 = BLOCKED.

## Тесты

```
cd bossman-core && python3 -m pytest \
  tests/test_trading_safety.py tests/test_trading_lookahead.py \
  tests/test_trading_claims.py tests/test_trading_paper_memory.py \
  tests/test_trading_pipeline_benchmark.py tests/test_trading_wiring.py -q
```
