# Финальный отчёт Video Studio

Дата: 2026-09-06. По последнему распоряжению владельца работа заканчивается на текущем редакторе. Остальные направления отменены. Полная реализация исходного ТЗ не заявляется.

- **BASE_SHA:** `debee6930f29595b84cea64d526b6f8bef139e8a`.
- **FINAL_SHA:** SHA коммита этого отчёта; воспроизводимо: `git log -1 --format=%H -- docs/video-studio/FINAL_REPORT.md`. Точный SHA также указан в сообщении о финальном push. Самоссылочный hash внутрь коммита не записывается.
- Ветка: `codex/video-studio`; отдельный checkout `C:\AiMaxBossman-video-studio`.

## IMPLEMENTED

Нативная страница BCC `#/video-studio`, связь с задачей/чатом, загрузка реальных исходников, многодорожечный монтаж, integer ticks и rational FPS, общие команды, история, undo/redo, optimistic concurrency и object leases. Реальные FFmpeg preview/export проходят проверку потоков, длительности и декодирования перед выдачей файла. Поддержаны эффекты, ключевые кадры, обработка звука, титры, SRT/VTT, прокси, аудиоволны и снимки scopes; точные границы — в [BACKEND.md](BACKEND.md).

Подключены OTIO и ограниченный Shotcut/MLT export с явным отказом при непредставимых эффектах. Локальные caption/tag search, существующие B-roll candidates и доказательства дубликатов ничего автоматически не удаляют. Перевод сохранённых EN→RU субтитров выполняет отдельная локальная Marian-модель; применение требует просмотра, сохраняет cue IDs и время.

Отдельный Qwen LoRA действительно обучен: 48 синтетических обучающих примеров, 24 отложенных, точные команды 0/24 → 13/24. Шесть семейств монтажных команд; произвольный монтаж и общий интеллект этой метрикой не измерены. См. [TRAINING.md](TRAINING.md). Веса остаются локально.

## Исправленные дефекты

1. Устаревший worker мог перезаписать состояние после смены fence; переходы завершения и восстановления теперь проверяют актуальную эпоху.
2. Истечение ресурса могло затереть продлённый heartbeat; UPDATE проверяет, что резерв всё ещё held и действительно просрочен.
3. Сбой между сохранением video job и enqueue оставлял вечный draft; идемпотентный повтор восстанавливает постановку через CAS.
4. Разветвлённый DAG вложенных sequences вызывал экспоненциальную проверку; обход O(V+E), предел глубины 16, развёрнутый render ограничен 4096 узлами.
5. Локальная LoRA удерживала RAM после выгрузки весов; inference перенесён в завершаемый дочерний процесс с timeout/cancel/reap.
6. Reels терял профильные размеры, VTT менял десятичные запятые в репликах, relink показывал старый кэш, открытый диалог принимал чужую revision. Исправления и реальные UI доказательства — в [UX_SPEC.md](UX_SPEC.md).

Общие auth, permissions, admission, budget и verification gates сохранены. `bossman-core` не менялся этим редакторским выпуском; BCC engine/resources получили необходимые интеграционные исправления. Управление сторонними Apps не включалось.

## AGENT_TOOLS_CONNECTED / CHAT_TO_EDITOR_E2E

24 зарегистрированных `video.*` инструмента: project create/open/inspect/interchange, timeline inspect/apply, media import/probe/relink/analyse, preview, export start/status/cancel, output verification, transcription и typed mutations. Они работают с проектом, привязанным к задаче, через тот же command layer. Передача произвольного пути вместо owner upload не разрешается.

Фактический chat → upload → canonical job → timeline → preview/export проверен интеграционным тестом. Настоящая обученная модель также дала проверенный UI draft volume=0.5: dry-run не изменил revision 9, явный Apply сохранил revision 10. Это доказательство конкретного сценария, не гарантированная правильность каждого ответа модели.

## PREVIEW_AND_EXPORT_VERIFIED / TEST_RESULTS

- Общий прогон редакторских Python тестов: **169 passed, 5 failed, 1 skipped**. Пять failures были ожиданием admission при нехватке RAM, а не успешным экспортом. Исходный результат не скрывается.
- После освобождения памяти четыре media/portable/tracking сценария повторно прошли с реальной телеметрией: **4 passed, 13.327 s**, `.audit-work/video-final-admission-retry-isolated.xml`. Первый повтор имел ошибку уборки общего Windows Temp; изолированный basetemp завершился успешно.
- Финальный trained bridge и оставшийся queued proposal: **32 passed, 32.22 s**, включая настоящую LoRA после изменения границы процесса; `.audit-work/video-release-trained-0609.xml`. Все пять ранее ожидавших admission сценариев прошли при повторе. Сводка исходного и повторных прогонов: [release-tests.json](evidence/release-tests.json).
- Pure UI: **10 passed**. Реальный Chromium: основной import/trim/color/preview/export/reload, **11 advanced checks**, concurrent stale-modal rejection, **5 AI/retrieval checks**. Для последнего translation job `a6298c9d43d541469f28eea35ab318a1` — 0 page errors, revision 18.
- Реальный Reels export: 1080×1920, 25 fps, 1 s, AAC 48 kHz stereo, декодирование и выборочные кадры проверены. Основной export: 1280×720, 1 s; preview 320×180.
- Отдельно выполнялись 12 skill-сценариев с настоящим FFmpeg, OTIO round trips, Shotcut/MLT renders, offline EN→RU, ASR fixture, tracking, reverse и hardware probes. Подробности и воспроизводимые тесты находятся в backend/UX/interchange документах.

Полный исторический набор всей ОС и CI точного финального SHA в этом завершающем прогоне не выполнялись. aiosqlite teardown warnings в части старых прогонов отдельно от успешности assertions. Единый безусловно зелёный full-suite результат не заявляется.

## PARTIAL / BLOCKED / KNOWN_LIMITATIONS

Полное ТЗ из 148 требований остаётся PARTIAL. Графические curves/wheels/masks, realtime post-effect scopes/meters, полноценные multicam/nesting/transcript UI и общий AI rough-cut не закончены. Генерация изображений/видео BLOCKED: проверенный провайдер не подключён. Поиск основан на тексте/тегах, не на визуальном понимании кадров. Перевод сейчас только EN→RU. HDR PQ/HLG отклоняется. Выходной звук фиксирован 48 kHz stereo. Shotcut export сознательно ограничен представимыми операциями.

Переносимый ZIP API round trip проверен; финальная отдельная браузерная проверка его импорта и proposal race между разными проектами не завершены. После reload специализированный вид некоторых analysis результатов может потерять action label, сохранив сырой результат. Обширные длинные проекты и все сочетания эффектов не аттестованы.

## Запуск и NEXT_REQUIRED_ACTIONS

Код подключён штатно через BCC. Для Python 3.11 окружения с зависимостями проекта и FFmpeg/ffprobe в PATH:

```powershell
Set-Location C:\AiMaxBossman-video-studio
$env:PYTHONPATH="$PWD\command-center;$PWD\bossman-core;$PWD"
$env:BCC_DATA_DIR="$PWD\.audit-work\video-ui-state"
python -m bcc.app --host 127.0.0.1 --port 8878
```

Открыть `http://127.0.0.1:8878/#/video-studio` и войти штатным способом BCC. Для отдельной установки следует выбрать свой data-dir. Модели/бинарники не скачиваются автоматически и не входят в Git. Опциональные настройки: `BOSSMAN_VIDEO_ASR_MODEL`, `BOSSMAN_VIDEO_CV_PYTHON`, `BOSSMAN_VIDEO_TRANSLATION_MODEL`, `BOSSMAN_VIDEO_TRANSLATION_PYTHON`, `BOSSMAN_VIDEO_TRAINED_TOKEN_FILE`. Токены не публикуются.

Локальный trained endpoint запускается Python окружением с Torch/PEFT: `python tools/video_studio/serve_adapter.py --adapter .audit-work/video-adapter-run01/adapter --report .audit-work/video-adapter-run01/report.json --port 8879`. Он слушает только loopback, требует token file и возвращает drafts. Для дальнейшего развития нужны отдельное распоряжение владельца, новый независимый holdout и приёмка перечисленных ограничений. Автоматическое продолжение этой работы остановлено.
