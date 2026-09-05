"""Generate the complete capability checklist; status changes require evidence."""
from pathlib import Path

# behavior | UI component | command/operation | verification | dependency
ROWS = '''
Создать проект|Медиатека / Новый|project.create|API→SQLite→повторное открытие|BCC DB
Открыть проект|Список проектов|project.open|Открытие сохранённого таймлайна в браузере|BCC DB
Переименовать проект|Название проекта|project.rename|UI→revision→restart|Command layer
Дублировать проект|Меню проекта|project.duplicate|Атомарная копия и неизменность оригинала|Command layer
Архивировать и восстановить проект|Меню проекта|project.archive|Архив не редактируется, восстановленный открывается|Command layer
Автосохранение каждой операции|Индикатор revision|commands|Перезагрузка после изменения|BCC DB
Восстановление после сбоя|Экран восстановления|project.open|Новый процесс читает историю и актуальную revision|BCC DB
История и сравнение версий|История|history/version|Различие документов и восстановление выбранной версии|BCC DB
Поиск отсутствующих файлов|Медиатека / Missing|media.inspect|Физически удалённый источник даёт missing|Media library
Перепривязка исходника|Медиатека / Relink|media.relink|Новый hash и проверенный export|Media library
Импорт нескольких файлов|Import|media.import|Два реальных MP4 и сохранённые метаданные|FFprobe
Drag-and-drop файлов|Drop zone|media.import|Настоящий browser drop→upload|Streaming upload
Папки медиатеки|Папки|media.update|Фильтрация и persistence|Project model
Теги медиатеки|Теги|media.update|Фильтрация и persistence|Project model
Поиск медиа|Search|media.inspect|Имя/тег возвращают нужные ID|Project model
Сортировка медиа|Sort|media.inspect|Проверка порядка по имени/длительности|Project model
Метаданные источников|Media info|media.probe|Сверка с фактическим ffprobe|FFprobe
Thumbnails|Media cards|media.prepare|Декодируемая картинка из исходника|FFmpeg
Аудиоволны|Audio timeline|media.prepare|Кэш и ненулевая волна у реального сигнала|FFmpeg
Прокси|Media preparation|media.prepare|Фактический низкоразрешённый файл, повтор использует кэш|FFmpeg
Переносимый проект без исходников|Project export|project.package|Распаковка и открытие проекта|ZIP
Переносимый проект с исходниками|Project export|project.package|Перенос в другой data-dir и export|ZIP/media library
Несколько видео- и аудиодорожек|Timeline|track.add|Независимые слои изображения и звука в render|FFmpeg
Перестановка дорожек|Track header|track.move|Изменяется порядок композитинга|Command layer/render
Mute|Track header|track.update|Поток/слой отсутствует в результате|FFmpeg
Solo|Track header|track.update|Только выбранные дорожки в результате|FFmpeg
Lock|Track header|track.update|Человек и агент получают отказ без mutation|Command layer
Перемещение клипа|Drag clip|clip.move|Изменённый start и соответствующие кадры|Command layer/render
Обрезка клипа|Trim handles|clip.trim|Точные source in/out и длительность|Command layer/render
Split|Timeline toolbar|clip.split|Полное покрытие без потери исходных кадров|Integer ticks
Ripple delete|Clip menu|clip.remove|Дальнейшие клипы сдвигаются на удалённый интервал|Command layer
Ripple trim|Clip menu|clip.trim|Дальнейшие клипы сдвигаются на изменение длительности|Command layer
Roll|Clip menu|clip.roll|Сохраняются стык и общая длительность|Command layer
Slip|Inspector|clip.slip|Меняется исходный диапазон при прежней позиции и длине|Command layer
Slide|Inspector|clip.slide|Соседи подрезаны, общая длина сохранена|Command layer
Snapping|Timeline magnet|clip.move|Привязка к playhead и стыкам при drag|UI
Группировка|Clip menu|clip.group|Группа перемещается вместе|Command layer
Связь аудио и видео|Clip menu|clip.link|Общие сдвиги сохраняют sync|Command layer
Отделение звука|Clip menu|clip.detach_audio|Два потока не удваивают громкость|FFmpeg
Копирование клипа|Clip menu|clip.copy|Новый ID и неизменный источник|Command layer
Маркеры|Timeline toolbar|marker.add|Точное время, label и повторное открытие|Project model
Выделение диапазона|Timeline ruler|range.set|Экспортируется именно заданный диапазон|FFmpeg
Вложенные последовательности|Sequence panel|clip.add|Рендер вложения и отказ на цикле|FFmpeg
Undo/redo|Toolbar|history.undo/redo|Обратимость действий человека и агента при monotonic revision|BCC DB
Масштабирование таймлайна|Timeline zoom|UI state|Координаты соответствуют времени после zoom|UI
Прокрутка и виртуализация|Timeline scroll|UI state|Длинный проект не создаёт все thumbnails в RAM|UI/cache
Изменение скорости|Inspector|clip.speed|Математика времени и декодированный export|FFmpeg
Reverse|Inspector|clip.reverse|Порядок выборочных кадров обратный|FFmpeg
Freeze frame|Inspector|clip.freeze|Одинаковые кадры на заданной длительности|FFmpeg
Speed ramp|Inspector / advanced command|clip.speed_ramp|Кусочные скорости и суммарная длительность|FFmpeg
Несколько камер и синхронизация|Multicam|multicam.sync|Измеренные offsets/timecode и сохранённый sync|Analysis backend
Position|Inspector|clip.transform|Положение объекта в фактическом кадре|FFmpeg
Scale|Inspector|clip.transform|Размер объекта в фактическом кадре|FFmpeg
Rotation|Inspector|clip.transform|Поворот объекта в фактическом кадре|FFmpeg
Crop|Inspector|clip.transform|Края удалены без подмены исходника|FFmpeg
Opacity|Inspector|clip.transform|Нижний слой виден через верхний|FFmpeg
Keyframes и easing|Inspector / keyframe lane|keyframe.set|Промежуточные кадры соответствуют интерполяции|FFmpeg
Переходы и crossfade|VFX|effect.apply|Два источника смешиваются на стыке|FFmpeg
Маски|VFX|effect.apply|Circle/rectangle ограничивают видимую область|FFmpeg
Chroma key|VFX|effect.apply|Заданный цвет становится прозрачным|FFmpeg
Режимы наложения|VFX|effect.apply|Пиксельное сравнение normal/multiply/screen|FFmpeg
Picture-in-picture|Inspector|clip.transform|Два разных видео одновременно в кадре|FFmpeg
Adjustment layers|Track controls|clip.add|Эффект меняет нижние слои только на своём диапазоне|FFmpeg
Стабилизация|VFX|effect.apply|Реальный анализ и измерение уменьшения движения|vidstab
Tracking|VFX|analysis.track|Координаты следуют движущемуся тестовому объекту|OpenCV
Экспозиция|Цвет|effect.apply|Измеренное изменение яркости|FFmpeg
Контраст|Цвет|effect.apply|Изменение распределения яркости|FFmpeg
Баланс белого|Цвет|effect.apply|Изменение каналов по заданным параметрам|FFmpeg
Насыщенность|Цвет|effect.apply|Изменение цветности на реальных кадрах|FFmpeg
Highlights/shadows|Цвет|effect.apply|Раздельное изменение светлых/тёмных областей|FFmpeg
Цветовые кривые|Цвет|effect.apply|Проверка заданных контрольных точек|FFmpeg
Цветовые колёса|Цвет|effect.apply|Контролы lift/gamma/gain меняют кадры|FFmpeg
LUT|Цвет|effect.apply|Применение настоящего cube и отказ на неверном LUT|FFmpeg
Scopes|Цвет / scopes|analysis.scopes|Значения вычислены из фактических кадров|Analysis backend
Сравнение до/после|Preview|preview.render|Одинаковое время с эффектами и без|FFmpeg
Копировать цветовые настройки|Inspector|effect.copy|Совпадающие параметры с независимыми ID|Command layer
Громкость клипа и дорожки|Звук|clip.audio/track.update|Измеренные уровни фактического аудио|FFmpeg
Audio fade|Звук|audio.process|Измеренные начальный и конечный RMS|FFmpeg
Панорама|Звук|clip.audio|Разница каналов соответствует pan|FFmpeg
Микшер и meters|Звук / mixer|analysis.audio|Фактические peak/RMS по дорожкам|FFmpeg
Audio automation|Keyframe lane|keyframe.set|Громкость меняется по кривой во времени|FFmpeg
Loudness normalization|Звук|audio.process|Повторное измерение LUFS после рендера|FFmpeg
EQ|Звук|audio.process|Изменение выбранного частотного диапазона|FFmpeg
Компрессор|Звук|audio.process|Снижение динамического диапазона|FFmpeg
Limiter|Звук|audio.process|Peak не превышает заданный предел|FFmpeg
Очистка шума|Звук|audio.process|Реальный фильтр и сравнение шумового участка|FFmpeg
Ducking музыки под речь|Звук|audio.process|Музыка тише на активной речи|FFmpeg
Контроль clipping и A/V sync|Export verification|output.verify|Предупреждения с измерениями, не безусловный отказ на тишине|FFprobe/analysis
Титры|Text controls|title.add|Реально вшитый читаемый текст|libass
Шаблоны титров|Text controls|title.add|Воспроизводимое оформление нескольких шаблонов|libass
Шрифты и оформление|Text inspector|clip.update|Шрифт/цвет/фон в результате|libass/fonts
Анимация текста|Text keyframes|keyframe.set|Положение/opacity титров меняются во времени|FFmpeg
Распознавание речи|ИИ / captions|captions.transcribe|Настоящая речь→временные текстовые сегменты|Whisper weights
Редактирование timed text|Caption editor|captions.edit|Сохранение временных диапазонов и preview|Command layer
Импорт и экспорт SRT/VTT|Caption editor|captions.import/export|Roundtrip и стандартные временные метки|Caption parser
Перевод субтитров|ИИ|captions.translate|Проверенный язык и сохранённые интервалы|Local/approved provider
Вшивание субтитров|Export|preview/export|Текст виден на нужных кадрах|libass
Текстовый монтаж|Transcript editor|transcript.cut|Остаются только выбранные интервалы источника|Command layer/render
Отбор сцен|ИИ|analysis.scenes|Реальные scene cuts и репрезентативные кадры|FFmpeg
Удаление пауз|ИИ|analysis.silence/transcript.cut|Паузы сокращены, речь сохранена|FFmpeg
Поиск дублей|ИИ|analysis.duplicates|Хеши/сходство источников и совпадающие интервалы|Analysis backend
Rough cut по заданию|Agent panel|video.timeline.apply|План→допустимость→реальный таймлайн|Agent command layer
Поиск момента по описанию|ИИ|analysis.search|Результат связан с текстом/кадром и реальным временем|Transcript/vision model
Короткие версии|ИИ|timeline.apply|Заданный диапазон/длительность экспортированы|Agent command layer
Адаптация 16:9 9:16 1:1|Export/sequence|sequence.update|Фактические выходные размеры и framing|FFmpeg
Автофрейминг|ИИ|analysis.reframe|Ключевой объект удерживается в целевом кадре|OpenCV/vision
Предложения B-roll|Agent panel|analysis.broll|Рекомендации с существующими media ID и причиной|Metadata/local model
Генерация изображений|ИИ|generation.image|Реальный provider artifact или точный blocker|Approved connected provider
Генерация видео|ИИ|generation.video|Реальный provider artifact или точный blocker|Approved connected provider
Улучшение голоса|ИИ / звук|audio.process|Локальная обработка и измерение результата|FFmpeg
Локальное удаление фона|ИИ / VFX|analysis.background|Маска реального объекта, без демонстрационной подмены|Local CV/model
Локальное повышение качества|ИИ / VFX|effect.apply|Обработанный файл и ясное отличие от AI superresolution|FFmpeg/local model
Место исполнения данные и стоимость|ИИ capability cards|capabilities|Показать local/cloud, egress, estimate/blocker|Existing policy/provider routing
Экспорт проекта и диапазона|Export dialog|export.start|Фактический файл указанного интервала|Canonical TaskEngine
Очередь рендера|Render queue|export.start/status|Существующие task_runs и корректная последовательность|Canonical TaskEngine
Разрешение FPS codec/container|Export dialog|export.start|Сверка ffprobe с выбранным профилем|FFmpeg
Bitrate/quality и аудио|Export dialog|export.start|Сверка codec bitrate sample rate channels|FFmpeg
Профили соцсетей|Export presets|export.start|Реальные 16:9/9:16/1:1 файлы|FFmpeg
Выбор hardware encoding|Export dialog|capabilities/export.start|Настоящий пробный encode доступного encoder|GPU/FFmpeg
Прогресс по стадиям|Render queue|export.status|Фактические out_time и stage events|EventBus
Отмена|Render queue|export.cancel|Subprocess завершён, job cancelled, нет ложного completed|TaskEngine
Повтор после исправления ошибки|Render queue|export.retry|Новый валидный запуск, прежний провал сохранён|TaskEngine/history
Проверка экспортированного файла|Verification panel|output.verify|Длительность/размер/звук/full decode/выборочные кадры|FFmpeg/FFprobe
Чёрные кадры тишина A/V sync|Verification panel|output.verify|Диагностика учитывает намеренный контент|Analysis backend
Плотная раскладка по референсу|Studio shell|UI|Визуальные screenshots без перекрытий|Browser
Вкладки Chat/Studio и рабочие пространства|Native tab strip|UI navigation|Состояние чата и редактора сохраняется при смене вкладки|BCC SPA
Изменяемые панели и сохранение layout|Resize handles|project.layout/UI|Reload восстанавливает размеры|Browser
Fullscreen preview|Preview toolbar|UI|Настоящий fullscreen API и выход|Browser
Контекстные меню горячие клавиши palette tooltips|Studio controls|Shared commands|Реальные действия через keyboard/menu|Browser
Русский и английский интерфейс|Language control|UI|Обе локализации основных состояний|Browser
Все состояния ошибок из ТЗ|Status/error surfaces|API errors|Missing/unsupported/device/provider/disk/cancel/recovery/conflict|Fault injection
Показать предложения агента до применения|Agent panel|commands dry_run|Нет mutation, видны затронутые ID|Command layer
Перейти к затронутому клипу|Agent panel|UI selection|Переход по ID по явному нажатию|UI
Undo агента и сравнение версий|History|history.undo/version|Восстановление предыдущего состояния|BCC DB
Защита ручного редактирования и playhead|Inspector/timeline|edit leases/revision|Агент не перезаписывает ручную правку и не перехватывает selection|BCC DB/UI
Маршрутизация шести намерений из ТЗ|Bossman Chat|video.chat|Каждый пример открывает связанный проект|Task routing
Теоретический вопрос не открывает редактор|Bossman Chat|intent classifier|Негативные примеры остаются обычным чатом|Task routing
Задание вложения переписка project-task-chat|Chat/Studio|video.chat/media.import|Перезапуск сохраняет связи и файлы|Canonical DB/storage
Повтор запроса не создаёт дубликаты|Bossman Chat|video.chat|Одинаковый operation_id→тот же task/project|Canonical DB
Fallback открытия вкладки|Chat result|UI link|Рабочая кнопка при недоступном автопереходе|BCC SPA
Единые typed tools UI и агента|Agent tool registry|video.*|Одинаковый command→одинаковое состояние при прежних разрешениях|ToolSpec/policy
Dry-run apply verify-state verify-file разделены|Agent/API|commands/inspect/output.verify|Job ID не засчитывается за экспорт|Command layer/TaskEngine
Skill и 12 сценариев обучения|Skill library|video-editing|Все сценарии содержат tool calls и проверяемый критерий|Native skills
Отрицательные сценарии из ТЗ|Tests|video.*|Missing ID/revision/disk/cancel/repeat/unknown/cloud|Integration tests
Существующие разрешения бюджет и recovery|Core integration|Canonical policies|Регрессии safety, не создан новый ledger/queue|BCC
Кэш по исходнику и параметрам|Media analysis|media.prepare|Повтор не вычисляет заново, изменение параметра меняет ключ|Content-addressed cache
Обучение весов отдельного адаптера|Local training|train_adapter.py|Изменены веса; фиксированный holdout до/после; promotion только по результату|PyTorch/PEFT
Совместимость с открытыми монтажными форматами|Project export/import|project.interchange|OTIO roundtrip и честный отчёт о потерях эффектов|OpenTimelineIO
'''.strip()


def main():
    root=Path(__file__).resolve().parents[2]
    target=root/"VIDEO_STUDIO_CAPABILITY_MATRIX.md"
    rows=[line.split("|") for line in ROWS.splitlines()]
    lines=["# Bossman Video Studio — обязательная карта функций", "",
        "BASE_SHA: `debee6930f29595b84cea64d526b6f8bef139e8a`.",
        "Статусы ниже первоначальные: реализация и проверка продолжаются. Наличие command/кнопки не означает завершение.",
        "Общий вход изменения: `{project_id,expected_revision,operation_id,command,dry_run}`; результат `{revision,project,changed_ids,warnings,artifacts,undo}`. Асинхронные операции возвращают `task_id/job_id`, затем фактический status и verification.", "",
        "| ID | Конкретное поведение | UI | Backend | Инструмент агента | Вход → результат | Проверка | Зависимости | Статус |",
        "|---|---|---|---|---|---|---|---|---|"]
    not_started={"Генерация изображений","Генерация видео","Перевод субтитров","Поиск момента по описанию",
                 "Автофрейминг","Предложения B-roll","Локальное удаление фона"}
    for i,(behavior,ui,op,verify,dependency) in enumerate(rows,1):
        status="NOT_STARTED" if behavior in not_started else "PARTIAL"
        lines.append(f"| VS-{i:03d} | {behavior} | {ui} | `{op}` | `video.{op}` через общий слой | Типизированные параметры → revision/artifact/status | {verify} | {dependency} | {status} |")
    target.write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(f"{len(rows)} explicit capability rows written")


if __name__=="__main__": main()
