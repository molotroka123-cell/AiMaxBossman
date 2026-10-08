# K1m6a 08.10: команды дня (продолжение роликов + доказательство обучения)

Статус: ПОДГОТОВЛЕНО, NOT_RUN. Этот файл — пошаговые команды к плану владельца
`docs/trading/K1M6A_RESUME_AND_PROVE_LEARNING_20261008.md`. Push документа ничего не запускает на ПК.

Один Bossman: те же backend :8801, память (`/api/memory`), модели и STOP, что у Jeff, Пульта и CMD.
Отдельной базы ученика нет. Реальной торговли нет.

## 0. Откуда продолжаем

- Остановочная точка: **#25 `8wItpyVUi2k`** (отчёты #23–#25 теперь в `docs/trading/k1m6a-training-results/interim-20261003/`).
- #1–#20 доставлены раньше; #22 открыт (16 расхождений ASR/VTT не прослушаны, vision 4 из 18 кадров); #23–#25 обработаны локально, уроки `UNVERIFIED_NOT_PROMOTED`; #26–#30 не запускались; у #5 нет исходника.
- Локальный архив может быть новее GitHub: сначала прочитать его `PROJECT_STATE.md`, `queue.json`, `catalog.json`, `DATA_FORMAT.md`.

## 1. Подготовка (ПК владельца, PowerShell, из корня рабочей копии на `main`)

```powershell
$A = "$env:LOCALAPPDATA\Bossman\CommandCenter\learning\k1m6a-youtube"
$X = "$env:USERPROFILE\Bossman\k1m6a-exam-20261008"; New-Item -ItemType Directory -Force $X | Out-Null
$PY = "<runtime python установленной сборки>"   # или python рабочей копии с установленным bossman-core
# что лежит на ПК и чего нет на GitHub (секреты, веса и кэши не открываются):
& $PY tools\local_completeness_manifest.py --root "$env:USERPROFILE\Bossman" --out "$X\local-manifest.json"
# свободное место и память перед тяжёлыми этапами:
Get-PSDrive C; Get-CimInstance Win32_OperatingSystem | Select FreePhysicalMemory
```

## 2. Зафиксировать десятку, сплит и пороги ДО работы

```powershell
& $PY -m bossman.trading_learning.k1m6a_exam pin --queue "$A\queue.json" --after 8wItpyVUi2k --out "$X\batch.json"
& $PY -m bossman.trading_learning.k1m6a_exam split --batch "$X\batch.json" --out "$X\split.json"
& $PY -m bossman.trading_learning.k1m6a_exam criteria --out "$X\criteria.json"
```

- `pin` берёт 10 следующих необработанных роликов после #25 из НАСТОЯЩЕЙ очереди. Меньше 10 → `BATCH_INCOMPLETE` (дособрать из того же разрешённого каталога, не выдумывать #31–35).
- `split`: 6 train / 2 validation / 2 locked test целыми роликами, хронологически; test — самые поздние. Изменить после — нельзя (sha256).
- `criteria`: пороги плана (поля ≥95%, locked ≥8/10, прирост ≥+1, 0 выдумок, после перезапуска ≥8 и падение ≤0.5, ≥30 ситуаций, ≥10 в test) закреплены хэшем.

## 3. Обработка роликов #26… (как раньше, штатный пайплайн)

Ролики из `batch.json` обрабатываются тем же локальным пайплайном, что #23–#25 (faster-whisper small CPU int8,
семантические кадры `smart_frames.json`, локальная vision-модель, извлечение уроков локальной моделью).
Тяжёлые модели по очереди на одном APU; Jeff должен отвечать. Ничего не перезаписывать в старых папках.

## 4. Ситуации экзамена и печать эталона Claude

```powershell
& $PY -m bossman.trading_learning.k1m6a_exam situations --archive $A --batch "$X\batch.json" --base $A `
    --out "$X\situations.jsonl" --segments-dir "$X\segments"
```

Claude (аудитор) ДО ответов ученика пишет `references.jsonl`: по строке на ситуацию
`{"situation_id", "fields": {имя: значение или "UNKNOWN"}, "scenarios", "confirm", "invalidate", "author_next": "пересказ своими словами", "alternatives"}`
— глядя на кадр и на речь ПОСЛЕ момента (её ученик не видит). Затем печать:

```powershell
& $PY -m bossman.trading_learning.k1m6a_exam seal --out "$X\SEAL.json" "$X\references.jsonl"
```

## 5. Три режима на одном locked test

```powershell
$EP = "http://127.0.0.1:11434/v1"; $M = "bossman-fast-qwen36-vision:latest"   # та же vision-модель, что в пайплайне
& $PY -m bossman.trading_learning.k1m6a_exam ask --situations "$X\situations.jsonl" --split "$X\split.json" `
    --mode BASELINE --seal "$X\SEAL.json" --criteria "$X\criteria.json" --endpoint $EP --model $M `
    --base $A --segments-dir "$X\segments" --out "$X\answers-baseline.jsonl"
```

- Затем учёба на train (раздел 6), потом `--mode LESSONS` с `--memory-url http://127.0.0.1:8801 --memory-token-file "$env:LOCALAPPDATA\Bossman\CommandCenter\token"`. Память должна быть настроена владельцем (страница «Память», путь к vault); без неё `/api/memory/*` честно отвечает 503, и режим LESSONS даёт BLOCKED.
- После штатного перезапуска Bossman — `--mode RESTART_TRANSFER` на новых невиденных ситуациях, без подсказок Claude.
- `--mode NO_RETRIEVAL` — отдельная проверка без памяти (прирост только с памятью = обучение workflow/retrieval, не весов).
- Обрезанный JSON ученика записывается `BAD_JSON` после 3 попыток, не «дочитывается».

## 6. Учёба на train/validation (тандем Bossman + Claude)

1. Bossman отвечает (режим BASELINE на train-ситуациях).
2. Claude оценивает по рубрике 0–2 × 5 (`fields, scenarios, author_match, no_fabrication, abstain_or_ask`) и отмечает ошибки — ПОСЛЕ фиксации ответа.
3. Bossman сам формулирует урок `WHEN / OBSERVE / CONFIRM / INVALIDATE / UNKNOWN / COUNTEREXAMPLE` с `provenance` (video_id, t_s) только из train/validation.
4. Проверка урока:
   ```powershell
   & $PY -m bossman.trading_learning.k1m6a_exam validate-lessons --lessons "$X\lessons.jsonl" --split "$X\split.json"
   ```
   `ACTIVE` — в память; `ARCHIVE_ONLY` (датированные цены) — в архив кейсов; `REJECTED` — переписать.
5. ACTIVE-урок пишется в штатную память Bossman (`POST /api/memory/write`, kind=lesson, project=k1m6a) и проверяется на ДРУГОМ train-примере.

## 7. Итог

```powershell
& $PY -m bossman.trading_learning.k1m6a_exam aggregate --split "$X\split.json" --criteria "$X\criteria.json" `
    --seal "$X\SEAL.json" --situations "$X\situations.jsonl" --references "$X\references.jsonl" `
    --scores "$X\scores.jsonl" --answers "$X\answers-baseline.jsonl" "$X\answers-lessons.jsonl" "$X\answers-restart.jsonl" `
    --out-dir "$X\result"
```

Вердикты: `EXAM_INSUFFICIENT`, `PROTOCOL_VIOLATION`, `NO_MEASURED_GAIN`, `FAIL`, `PASS_PRELIMINARY` (не больше: малая выборка).
Файлы: `result\paired_results.json`, `result\before_after.csv`.

## 8. Два часа без надзирателя

Bossman сам обрабатывает новые материалы 2 часа без подсказок; журнал `unsupervised-log.json`
(`started_at, ended_at, teacher_hints:false, stop_tested, stop_respected, items[{video_id,status,retries,verified}]`):

```powershell
& $PY -m bossman.trading_learning.k1m6a_exam unsupervised --log "$X\unsupervised-log.json"
```

`UNSUPERVISED_WORKFLOW_PASS` относится только к этому обработчику и оценённым задачам, не к автономной торговле.

## 9. Что Claude делает параллельно (облако/второй чат)

- Не ждёт ученика: по тем же 10 роликам готовит эталон (`references.jsonl`) и печатает его до ответов.
- Сверяет с собственным аудитом: где ученик систематически ошибается (OHLC open вместо текущей цены, единицы CVD/OI, таймфрейм) — превращает в урок-кандидат для ученика, а не правит ответ за него.
- Locked test никогда не попадает ученику и в память; при провале не пересдаём тот же test.
- В GitHub — только очищенные метаданные и агрегаты (без медиа, полных транскриптов, длинных цитат, путей пользователя).

## Что NOT_RUN на момент push

Всё в этом файле: обработка #26+, эталон, экзамен, уроки, перезапуск, двухчасовой прогон. Код экзамена покрыт
18 тестами на синтетике (`bossman-core/tests/test_k1m6a_exam.py`), на настоящих роликах не запускался.
