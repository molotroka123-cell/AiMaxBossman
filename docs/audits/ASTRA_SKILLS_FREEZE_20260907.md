# Astra: аудит остаточных блокеров + пакет скиллов

## Область проверки и границы

Дата: 2026-09-07. Это аудит прочитанного кода и GitHub CI, не новый полный
прогон приложения. Основная рассмотренная линия PR #37:
`dcdb223192622f89760ac73696871b7e81023188`, затем diff до
`278a8aceb06f8a11387808608c24f851c39650dc`.
База отдельного пакета скиллов: `b57b4ce981458581600adc2ee6836abca58be6fb`.
Новые изменения Opus после этих снимков необходимо перепроверить.

GitHub PR #37: https://github.com/molotroka123-cell/AiMaxBossman/pull/37
При проверке последней указанной версии фактический merge checkout был
`ec0825056234856b45212d7ad1dd2473d0864b6c`, а не head PR.
Результаты по старому SHA не переносятся на новые коммиты автоматически.

## Подтверждённый прогресс

На `dcdb223` код уже содержит Windows extras, нормализацию Gateway `/v1`,
UTF-8 environment для дочерних приложений и новый OpenRouter connect/catalog
flow. Старые открытые записи в описании PR не означают, что эти фиксы отсутствуют.
Command Center CI run `34072319689` был SUCCESS, включая Windows paths и
Video Studio descriptor boundary. Это не приёмка рабочего стола владельца.

В позднем diff появились изменения таймаутов оператора/owner stop, autosave
Web Designer и обработки контейнера/ошибок видео. Их наличие не доказывает
полную корректность; результаты ниже относятся к наблюдавшимся проверкам.

Источники:
- https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34072319689
- https://github.com/molotroka123-cell/AiMaxBossman/commit/dcdb223192622f89760ac73696871b7e81023188
- https://github.com/molotroka123-cell/AiMaxBossman/commit/278a8aceb06f8a11387808608c24f851c39650dc

## AF-01 — P1: приёмка видеопути всё ещё красная

**Доказательство CI:** Editors user safety run `34078123046`, job
`101608121353`, checkout `ec082505...`: **1 failed, 4 passed**.
Падает `test_video_ui_import_trim_undo_preview_export_restart` на
`play_preview_to_end`, ожидание 15000 ms. Это конкретный результат после
последнего рассмотренного видеофикса, а не повтор старой жалобы.
Первопричина нового timeout данным логом не установлена.

Источник: https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34078123046

## AF-02 — P1: fallback исправлен не на всём пользовательском пути

**Подтверждено чтением кода:** в `278a8ace` тест для WebM вызывает API через
`page.evaluate` и перезагрузку страницы, обходя кнопку создания preview.
Рабочая кнопка вызывает `startExport(true)`, а функция по умолчанию выбирает
`container='mp4'`. Автоматический выбор совместимого контейнера в самой кнопке
не виден. Успешный WebM сервисный путь не закрывает UI-путь в браузере без
поддержки полученного MP4. Это отдельная проблема; не объявляем её доказанной
причиной AF-01 без browser/media evidence.

Нужно выбирать формат/кодеки в production UI или дать реально доступное
управление fallback. Оба варианта тестировать через обычные кнопки; отрицательный
контроль — production без выбора формата должен проваливать этот тест.
Не обобщать результат одной сборки браузера на «все Linux-сборки».

Источники:
- https://github.com/molotroka123-cell/AiMaxBossman/blob/278a8aceb06f8a11387808608c24f851c39650dc/command-center/ui/pages/video_studio.js
- https://github.com/molotroka123-cell/AiMaxBossman/blob/278a8aceb06f8a11387808608c24f851c39650dc/command-center/tests/test_editors_user_acceptance.py

## AF-03 — P1: OpenRouter при существующем не-OpenRouter провайдере

**Подтверждено чтением кода, без live-эксплуатации:** `openrouter.js` показывает
создание провайдера только при `!providers.length`. Если в базе лишь Ollama,
выбор падает на `providers[0]`; панель пишет ключ в выбранный provider_id.
В `features/openrouter.py` `_openrouter_provider` проверяет существование строки,
но не её принадлежность OpenRouter; `PATCH /key` обновляет ключ этой строки.
Путь «есть только Ollama → вставить ключ OpenRouter» не эквивалентен пустой базе.
Он требует негативного теста на неизменность постороннего провайдера.
Не использовались настоящие ключи, внешние эффекты не воспроизводились.

Нужно проверять каноническую identity провайдера и на UI, и на сервере, иметь
Connect при отсутствии именно OpenRouter, сохранить другие ключи/endpoint,
протестировать повторный Connect и конкурентный клик.

Источники (не изменены просмотренным diff до 278a8ace):
- https://github.com/molotroka123-cell/AiMaxBossman/blob/dcdb223192622f89760ac73696871b7e81023188/command-center/ui/pages/openrouter.js
- https://github.com/molotroka123-cell/AiMaxBossman/blob/dcdb223192622f89760ac73696871b7e81023188/command-center/bcc/features/openrouter.py

## AF-04 — P1 для сертификации: FULL не измеряет полный Bossman

`tools/intelligence_preservation_run.py` обращается к Ollama напрямую;
`Task.rendered` добавляет короткий system prompt, текстовый context и список
имён инструментов. Здесь не выполняется полный production-loop Bossman с
реальными инструментами/контекстным механизмом. Такой прогон полезен как
prompt-smoke, но не доказывает сохранение качества всей обвязкой.

Режим FULL должен вызывать реальный путь выполнения; каждый lane должен иметь
наблюдаемые trace/идентификаторы конфигурации. Нужны один model/config/dataset,
предварительно заданная методика, поэлементные парные результаты и честная
оценка достаточности выборки. Не заполнять PASS руками и не ослаблять gate.

Источники:
- https://github.com/molotroka123-cell/AiMaxBossman/blob/dcdb223192622f89760ac73696871b7e81023188/tools/intelligence_preservation_run.py
- https://github.com/molotroka123-cell/AiMaxBossman/blob/dcdb223192622f89760ac73696871b7e81023188/tools/intelligence_preservation_gate.py

## AF-05 — недостающая измеренная приёмка

Intelligence Preservation run `34072319679` не нашёл
`docs/benchmark/intelligence-preservation-current.json` и завершился с exit 2;
контракт самого gate прошёл. Поздний run `34078123007` тоже был FAILURE.
Нельзя делать вывод о деградации модели по отсутствию измерения.

Scorecard прямо пишет, что production scheduler не вызывает `rank()`, standing
autonomy выключена, реальный model measurement/soak/owner Windows не выполнены.
Поэтому `IMPLEMENTATION_COMPLETE (repo-local)` нельзя пересказать как
«V5 полностью работает». Последний прочитанный CHECKPOINT_2 помечает сценарий A
незавершённым; live runtime был остановлен по распоряжению владельца.

Для exact-SHA evidence заранее решить хранение результата: коммит с новым
JSON меняет SHA. Предпочтительны внешние CI artifacts, связанные с реально
проверенным source SHA/tree; нельзя просто подменить evaluated_sha в старом
отчёте или объявить head SHA проверенным после checkout merge SHA.

Источники:
- https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34072319679
- https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34078123007
- https://github.com/molotroka123-cell/AiMaxBossman/blob/dcdb223192622f89760ac73696871b7e81023188/docs/v5/V5_RELEASE_SCORECARD.md
- https://github.com/molotroka123-cell/AiMaxBossman/blob/dcdb223192622f89760ac73696871b7e81023188/docs/testing/acceptance-run-20260906/CHECKPOINT_2.md

## Вердикт

`FREEZE_CERTIFICATION = NO-GO` по рассмотренному снимку.
Это не новая оценка всего репозитория и не заявление о числе всех P0/P1.
В первую очередь: реальное видео, изоляция провайдеров, точное evidence.
Затем согласованные Windows/model/soak проверки с разрешением владельца.
Не переписывать историю, не чистить ветки и не мигрировать UI-фреймворк ради
этого закрытия. Загрузка скиллов не устраняет перечисленные дефекты сама по себе.
