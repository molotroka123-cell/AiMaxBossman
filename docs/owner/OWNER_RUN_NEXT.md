# Owner-Run — следующий прогон: профиль `self-improve-mvcr`

> **Статус этой инструкции.** Команда подготовлена в облаке (CLOUD_PREPARE).
> Проверена только автотестами (`tests/test_owner_run_self_improve.py`) с
> детерминированной тестовой моделью и поддельными скриптами-соседями на Linux.
> На Windows CI этот профиль отдельным шагом **не прогонялся**, на железе
> владельца — **не запускался ни разу**. Ни один статус ниже не означает
> OWNER_HARDWARE_CERTIFIED. Веса моделей раннер не меняет; MVCR он не подаёт.

Всё запускается одним и тем же `Owner-Run.cmd` из распакованного архива
(`BOSSMAN-Windows-x64-<sha>\Owner-Run.cmd`). Отдельного лаунчера нет. Клон
репозитория, `PYTHONPATH` и пути вида `C:\Users\<имя>\Bossman\wt-*` не нужны:
раннер и его соседи лежат в `app-support\`.

## 1. Точные команды по порядку

Откройте `cmd` в папке архива и выполняйте по одной; после каждой читайте итог
и строку «следующая команда».

```bat
Owner-Run.cmd self-improve-mvcr plan
Owner-Run.cmd self-improve-mvcr preflight
Owner-Run.cmd self-improve-mvcr bootstrap
Owner-Run.cmd self-improve-mvcr bootstrap --allow-download --profile <ID из plan>
Owner-Run.cmd self-improve-mvcr compare
Owner-Run.cmd self-improve-mvcr mvcr --owner-folder "D:\MVCR\документы" --facts "D:\MVCR\facts.json"
Owner-Run.cmd self-improve-mvcr self-improve --budget-minutes 40
Owner-Run.cmd self-improve-mvcr report
```

Либо всё подряд с пропуском уже сделанного (одна и та же команда годится для
первого запуска и для продолжения):

```bat
Owner-Run.cmd self-improve-mvcr run --owner-folder "D:\MVCR\документы" --facts "D:\MVCR\facts.json"
Owner-Run.cmd resume
```

Остановить: `Owner-Run.cmd stop`. Посмотреть состояние: `Owner-Run.cmd self-improve-mvcr status`.

Полезные параметры (все необязательные):

| параметр | зачем |
|---|---|
| `--models-dir D:\models` | папка моделей (иначе `BOSSMAN_MODELS_DIR` или `%LOCALAPPDATA%\Bossman\CommandCenter\models`) |
| `--reuse-dir D:\old-models` | где уже лежат скачанные GGUF — их переиспользуют, а не качают |
| `--endpoints http://127.0.0.1:8081,http://127.0.0.1:8083` | какие llama-server проверять и сравнивать |
| `--compare-endpoint URL` | сравнивать только этот эндпоинт (можно несколько раз) |
| `--case <файл>` | кейс для self-improve (иначе первый `app-support\self_improve_cases\*.json`) |
| `--telegram start` | запустить Telegram-поллер, если он ещё не работает |
| `--run-dir <папка>` | своя папка прогона (по умолчанию `%LOCALAPPDATA%\Bossman\CommandCenter\owner-run\self-improve-mvcr`) |
| `--redo <шаг>` | осознанно повторить шаг (в том числе UNKNOWN_OUTCOME) |

## 2. Что делает каждая стадия

Каждая стадия заканчивается одним статусом: PASS / WARN / FAIL /
OWNER_REQUIRED / NOT_RUN / BLOCKED (и UNKNOWN_OUTCOME — см. раздел 5) — с
подсказкой «куда дальше».

1. **plan** — ничего не меняет: печатает стадии, что будет скачано
   (`model_fetch.py plan`), сколько места нужно и сколько свободно, каких
   скриптов не хватает, что требует вашего разрешения. Папку прогона не создаёт.
2. **preflight** — доктор (`bossman_doctor.py`); эндпоинты моделей
   (`/v1/models` + крошечное завершение); план переиспользования моделей и
   `model_fetch.py runtime-check`; Bossman API (`/api/identity`) и
   `GET /api/coding-tasks/readiness` — PASS только если там `available` **и**
   запись о настоящем рукопожатии сайдкара; Telegram-поллер — если
   `poller.lock` держит живой процесс, раннер пишет «уже работает» и второй не
   запускает. Устаревший `poller.lock` (файл без живого держателя) запуску не мешает.
3. **bootstrap** — снова `model_fetch.py plan`; качает **только** профили со
   статусом «отсутствует/битый» и **только** те, что вы назвали `--profile`, и
   только с `--allow-download`. Здоровые (REUSED) модели не перекачиваются. После
   скачивания — `model_fetch.py verify`. Не хватает места — BLOCKED до скачивания.
4. **compare** — `model_bakeoff.py` на каждом живом эндпоинте: те же 7 задач A–G,
   что в лаборатории 22.09 (баг, навигация, патч с регрессией, выбор инструмента,
   JSON, восстановление после ошибки инструмента, длинный контекст); проверка
   исполнением кода модели во временной папке. Итог — `compare\summary.json`.
   Модель для продукта автоматически не выбирается.
5. **mvcr** — `mvcr_prepare.py --owner-folder … --facts … --out … --json`.
   Ожидаемый итог: `WAIT_APPROVAL` (пакет готов — проверить и подать **вручную**),
   `PARTIAL_MISSING_DATA` (дополнить данные и повторить mvcr) или `BLOCKED`.
   Если скрипт когда-нибудь сообщит о подаче — это FAIL: раннер не подаёт никогда.
6. **self-improve** — `self_improve_lab.py`: `explore` → `compare` → `lesson`,
   затем **полный перезапуск Bossman** (процесс из `desktop.lock` останавливается,
   запускается заново, успех доказывается сменой `started_at` в `/api/identity`),
   затем `transfer`. Каждая фаза — не больше `--budget-minutes` (по умолчанию 40).
   Если процесс Bossman не найден, раннер сам не перезапускает: OWNER_REQUIRED —
   закройте Bossman, запустите `Start-Bossman.cmd`, затем `Owner-Run.cmd resume`
   (перезапуск засчитается по `started_at`).
7. **report** — `report.md` (по-русски) и `report.json` в папке прогона, со
   следующей командой.

Скрипта-соседа нет (`model_fetch.py`, `mvcr_prepare.py`, `self_improve_lab.py`)
— стадия **NOT_RUN** с причиной, никогда не PASS.

## 3. Что требует вашего разрешения

- скачивание моделей — `--allow-download` **и** явный `--profile <ID>`;
- подача MVCR — только вы, руками; раннер останавливается на WAIT_APPROVAL;
- перезапуск Bossman в фазе self-improve (запустите эту стадию, только когда
  готовы, что окно Bossman закроется и откроется снова);
- запуск Telegram-поллера — только с `--telegram start`;
- повтор шага с внешним действием после UNKNOWN_OUTCOME — только `--redo <шаг>`.

## 4. STOP

- `Owner-Run.cmd stop` — кладёт файл `STOP` в папку прогона и ждёт до 60 с,
  пока раннер остановится между шагами; раннер убивает **всё дерево** процессов,
  которое запустил сам (детей и внуков), и пишет `stop-report.json`: какой шаг
  шёл, какие pid убиты. Если раннер уже мёртв, `stop` сам добивает
  зарегистрированных детей — сирот не остаётся.
- То же самое — создать пустой файл `STOP` в папке прогона вручную.
- Службы, которые раннер запускал намеренно (Telegram-поллер по
  `--telegram start`, перезапущенный Bossman), `stop` не трогает — они
  перечислены в `stop-report.json` как оставленные работать.

## 5. Продолжение (resume)

`Owner-Run.cmd resume` проходит стадии по порядку по `state.json`:

- завершённые повторяемые шаги с PASS/WARN пропускаются; с другими статусами —
  повторяются (они безопасны: чтение, докачка, подготовка пакета);
- шаг с внешним действием (фазы explore/compare/lesson/transfer), который уже
  выполнялся, **не повторяется никогда** — только `--redo`;
- такой шаг, прерванный посередине (STOP, выключение ПК), помечается
  **UNKNOWN_OUTCOME**: проверьте по логам (`logs\`), что он сделал, и только потом
  `Owner-Run.cmd self-improve-mvcr resume --redo self-improve:explore`;
- модели, которые model_fetch назвал REUSED/здоровыми, не качаются заново;
- второй Telegram-поллер не запускается (проверка замка ядра);
- второй раннер в той же папке прогона не стартует («уже идёт прогон»).

## 6. Откат

Профиль ничего не меняет в установленном продукте, кроме:

- скачанных моделей в `--models-dir` — удалить их файлы, если не нужны;
- перезапуска Bossman — просто запустите `Start-Bossman.cmd`;
- результатов coding-задач self-improve — они в изолированных клонах без remote,
  в продукт ничего не вливается без вас;
- папки прогона — её можно удалить целиком (следующий запуск начнёт заново).

Откат самой сборки Bossman — по `docs\owner\ROLLBACK_RU.md`.

## 7. Что приложить к отчёту о дефекте

Папку прогона целиком без `models\`: `state.json`, `report.md`, `report.json`,
`stop-report.json`, `logs\`, `compare\summary.json`, `mvcr\` (если в нём нет
личных документов — иначе только `report.json`). И `Collect-Diagnostics.cmd`.
