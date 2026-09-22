# Coaching exam 2026-09-22 — Phase A frozen, live run pending hardware

Реализация разделов 6 и 9 ТЗ владельца (`docs/evo/LOCAL_QWEN_APPRENTICE_BENCHMARK.md`,
Phase A/B/C/F) на четырёх РЕАЛЬНЫХ дефектах, закрытых учителем на машине владельца
в сессии 2026-09-22.

Статус: харнесс собран и доказан на MOCK-ученике. **Живой прогон на Qwen — NOT_RUN**:
локальные модели выключены, видеокарта занята чужим прогоном. Числа по способностям
Qwen в этом каталоге отсутствуют и не выдуманы.

## Что где лежит

| что | путь |
|---|---|
| манифест экзамена (Phase A, заморожен) | `owner-repair/coaching-exam-20260922/manifest.json` |
| раннер | `tools/coaching_exam.py` |
| тесты харнесса на MOCK-ученике | `command-center/tests/test_coaching_exam.py` |
| доказательство корректности кейсов (без модели) | `owner-repair/coaching-exam-20260922/mock/self_check.json` |
| прогон MOCK-ученика по реальным кейсам | `owner-repair/coaching-exam-20260922/mock/report.{json,md}` |
| ОТВЕТЫ, патчи, скрытые тесты, подсказки | **не в репозитории**: `C:\Users\asd\Bossman\exam-sealed-20260922` |
| lab-worktree, где ломается код | `C:\Users\asd\Bossman\wt-lab` (ветка `lab/coaching-exam-20260922`) |

Раннер не пишет свою подсистему обучения: уроки идут через канонический
`learning.lessons.LessonBook` поверх `learning.trace.LearningStore`, бэкенды и
константы переиспользуются из `tools/coaching_runner.py`.

## Четыре кейса

| id | split | дефект | видимые тесты на сломанном коде | скрытый верификатор |
|---|---|---|---|---|
| TRAIN-1 | train | кириллический запрос уходит в англоязычный энкодер FLUX/SDXL без перевода | **red** (`test_companion_media_models.py`) | 6 тестов |
| TRAIN-2 | train | первый листинг Studio падает по короткому таймауту, пока ядро пересчитывает sha256 весов | green (дефект не виден существующему набору) | 4 теста |
| HOLDOUT-1 | holdout | PowerShell-образная склейка строк при дописывании пары в env-файл | green | 6 тестов |
| HOLDOUT-2 | holdout | байты видео уходят в Telegram без проверки контейнера MP4 | green | 5 тестов |

Для каждого кейса в манифесте зафиксированы: `base_sha`, текст задачи, разрешённые
инструменты, бюджет (попытки/подсказки/время/токены), видимые тесты и ожидаемый их
цвет на сломанном коде, скрытый верификатор (только имя и sha256), ожидаемое
наблюдаемое поведение, ограничения безопасности, разрешённые к правке файлы и
манифест доказательств.

## Как обеспечена изоляция holdout

1. **Ответы не в репозитории.** Break/fix-правки, лестница подсказок, скрытые тесты и
   синтетическая фикстура лежат в `exam-sealed-20260922` — каталоге вне всех git-worktree.
   В репозитории только sha256 каждого файла (`manifest.sealed_sha256`, 23 файла).
   Несовпадение хэша останавливает прогон (exit 4), а не «чинится» молча.
2. **Скрытый тест HOLDOUT-2 физически вырезан** из `test_companion_media_models.py`
   в lab-worktree (`test_video_with_non_mp4_bytes_is_never_sent`) и живёт только в seal.
   Тесты копируются в рабочее дерево на время запуска верификатора и удаляются сразу после.
3. **Ученик не имеет доступа к файловой системе и к git.** Раннер сам подаёт в промпт
   только объявленные окна файлов; `git`, `shell`, `network` в `allowed_tools` = false,
   и это не декларация, а конструкция: у бэкенда нет инструментов.
4. **Память.** Урок holdout-кейса (`LSN-TG-ENV-CONCAT`) не попадает в
   `lessons_seeded`, а `seed_lessons` отдельно ОТКАЗЫВАЕТ ему по списку
   `lessons_forbidden` и пишет отказ в отчёт. Если урок всё же оказался в хранилище,
   `ExamRunner.guard_holdout` роняет кейс с `HoldoutLeak` — молча помочь ученику нельзя.
5. **Логи и отчёт.** Вывод скрытого верификатора по holdout редактируется до вердикта
   (`[REDACTED ...]`). Тест `test_full_mock_run_writes_a_report_with_both_profiles`
   проверяет, что имён скрытых тестов в `report.json` нет.
6. **На holdout нет подсказок и нет учительского патча**: `max_hints = 0`,
   `teacher_patch_allowed = false`. Исход только `STUDENT_UNASSISTED_PASS` или `FAIL`.

## Цикл прогона

```
ученик исследует -> воспроизводит (видимые тесты) -> пишет patch (SEARCH/REPLACE)
 -> добавляет регрессию -> раннер прогоняет видимые тесты и регрессию
 -> учитель проверяет (аудит: правка тестов, ослабление проверок, слепая регрессия)
 -> ученик исправляет замечания -> НЕЗАВИСИМЫЙ верификатор (скрытые тесты) решает
```

Подсказки ступенчатые и только на train: `broken_invariant` -> `cause_class` ->
`verification_strategy`. Учительский патч — только после исчерпания бюджета попыток и
всегда отдельной строкой `TEACHER_PATCH`, никогда не как успех ученика.

Статусы: `STUDENT_UNASSISTED_PASS`, `STUDENT_COACHED_PASS`, `TEACHER_PATCH`, `FAIL`.

Сравнение A/B: профили `no_lessons` и `with_lessons` при одной и той же модели,
инструментах, бюджете и наборе кейсов; между ними хранилище уроков перезапускается
(новый `LessonBook` на том же каталоге), то есть проверяется реальный путь retrieval.

Метрики: unassisted/coached solve rate, teacher-patch rate, fail rate, попытки,
подсказки, `memory_lookup_correct_rate` (ученик обязан назвать номера уроков, которые
использовал), `repeated_failures` (повтор той же сигнатуры отказа), tool errors, время,
объём промптов/ответов. `WEIGHTS_UNCHANGED` печатается на каждом прогоне: LoRA и любое
изменение весов в 1.0 запрещены.

## Доказательство харнесса без модели

```
# 1. кейсы корректны: скрытый верификатор красный на сломанном коде и зелёный на починенном
../wt-release/.venv/Scripts/python.exe tools/coaching_exam.py --self-check \
    --out owner-repair/coaching-exam-20260922/mock

# 2. харнесс различает умного и тупого ученика, считает подсказки, не течёт на holdout
cd command-center && PYTHONPATH="$PWD;$PWD/../bossman-core" \
    ../../wt-release/.venv/Scripts/python.exe -m pytest -q tests/test_coaching_exam.py -p no:cacheprovider
```

## Живой прогон (запускает владелец, когда освободится железо)

Одна команда. Она сама находит модели в рантайме (`GET /v1/models`, имена из
документации не используются), прогоняет четыре кейса в двух профилях на MAIN и FAST
и пишет `report.json` + `report.md` рядом с манифестом:

```powershell
cd C:\Users\asd\Bossman\wt-exam; `
C:\Users\asd\Bossman\wt-release\.venv\Scripts\python.exe tools\coaching_exam.py `
  --students MAIN=http://127.0.0.1:8081/v1 FAST=http://127.0.0.1:8082/v1 `
  --lab C:\Users\asd\Bossman\wt-lab `
  --sealed C:\Users\asd\Bossman\exam-sealed-20260922 `
  --out owner-repair\coaching-exam-20260922
```

Перед запуском: `wt-lab` должен быть чистым (раннер откажется ломать грязное дерево),
а модели — подняты с `--jinja --reasoning off` (LSN-LLAMACPP-REASONING-OFF).

Коды выхода: `0` — прогон состоялся, `3` — ни один эндпоинт не отвечает
(`LOCAL_EXAM_NOT_MEASURED`, числа не выдумываются), `4` — seal изменился,
`5` — `--self-check` показал, что кейс перестал воспроизводиться.

## Что остаётся NOT_RUN до живого запуска

- способность Qwen решать эти четыре кейса (unassisted / coached / teacher-patch rate);
- эффект уроков (сравнение `no_lessons` против `with_lessons`) на реальной модели;
- перенос урока на holdout без подсказок;
- правильность поиска в памяти реальной моделью;
- реальное время и расход токенов.

Всё перечисленное в `mock/report.json` помечено `MOCK` и измерением модели не является.
