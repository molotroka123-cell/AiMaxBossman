# Bossfield: инструкция Claude Code для общей сборки 1.2

**Состояние на 23.09.2026:** код подготовки проекта, маршрутизация Seedance 2.5, безопасный каталог и монтаж; платный запрос и локальный прогон на машине владельца **не выполнялись**. Концепт: [Bossfield](assets/bossfield-concept.jpg). Это оригинальная идея бренда Bossfield, не официальный продукт Higgsfield и не копия его закрытого сервиса.

## Для интегратора Claude: сначала обнови канонику

1. `git fetch origin`; проверь реальный HEAD `origin/release/bossman-owner` и все коммиты после `0a7a9aef33422cda36334e8563aa3519c63a69eb`. Наша ветка `codex/bossfield-amd-30s` базируется на этом SHA; никогда не перезаписывай новые коммиты Claude или чужие файлы принудительным push.
2. Прочти `AGENTS.md`, `docs/terminal/TERMINAL_RUN_1_2_MASTER.md`, `docs/evo/BOSSMAN_1_1_NORTH_STAR.md`, `CLAUDE_NEXT_ACTION.md`. Выполни review диффа этой ветки и интегрируй через обычный merge/cherry-pick в обновлённую канонику. Если каталог или OpenRouter adapter изменены Claude, перенеси только две узкие добавки: зарегистрированный `seedance-2.5` и маршрут `/videos`; сохраняй его более свежую логику. Запусти каталог/Studio/terminal regression suite.
3. Это **тот же Bossman**, его Studio, Gateway, проекты, файлы, модельный каталог, разрешения, budget/egress/STOP и журнал; терминал лишь дополнительная поверхность. `tools/bossfield_owner_run.py` — вспомогательный генератор фиксированного задания и независимый монтаж, не новый brain/API/хранилище. Установи его в существующую CLI-операцию через backend, когда Terminal Run готов; никогда не обходи Studio direct OpenRouter HTTP. Приватный рабочий проект вне git.
4. Отметь настоящую ступень North Star по фактическим evidence, не называй монтаж самообучением: `SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT → SELF_REPAIR_SINGLE_CYCLE_PASS → SELF_REPAIR_3_CYCLE_PASS → TRANSFER_MEASURED_GAIN → 24H_SOAK_PASS → 48H_SOAK_PASS → WEEK_MODE_READY → REVENUE_CAPABLE_PILOT`. Успешный teacher-run и самостоятельный перенос на новую задачу учитывай раздельно.

## Качество: 15 секунд Seedance 2.5 + 3 × 5 секунд локально

Облачный основной кандидат — **Seedance 2.5**, а не Mini: его опубликованный контракт поддерживает 15 с, 720p, 9:16/16:9, кадры начала/конца и референсы. 2.0 стандарт оставь сравнимым кандидатом при наблюдаемом неуспехе 2.5; Mini не включай скрытым fallback. Живую доступность/цену/контракт проверь непосредственно перед платным запуском, выстави owner budget и egress consent, а затем одну малую проверку реальным API. Каталог нарочно `UNVERIFIED`, `enabled=false`, `price=unknown`; спецификация не выдаётся за доказанную интеграцию.

Локальный кандидат на **AMD Ryzen AI Max+ 395 / Radeon 8060S / 128 GB shared RAM, <=88 GB выделения GPU** — уже существующий `sdcpp:wan2.2-ti2v-5b` через Vulkan: `command-center/bcc/studio/providers/sdcpp.py`, `models/media/MANIFEST.json`, `BOSSMAN_SDCPP_BIN`, `BOSSMAN_MEDIA_MODELS`. В каталоге предел 81 кадр / 16 fps ≈ 5.06 секунды: три клипа по 81 кадру и 16 fps, каждый урезается при монтаже до 5 с. Зафиксируй `peak_memory`, codec, sha, CLI и кадры: если >88 GB, OOM, шум, искажение лица или cadence fails — **BLOCKED**, уменьши разрешение/steps и повтори тест, не заявляй готовность. Ранний smoke 832×480 /20 steps описан в текущем провайдере; это не гарантия 15 секунд или качества на машине владельца. Не ставь `--diffusion-fa` / `--vae-tiling` пока тест не покажет полезность. LTX-Desktop 2.5 на AMD объявляет API-only для Windows/Linux без CUDA: не подменяй этим локальный прогон.

Режиссура важнее одного большого промпта: из истории владельца сделай сценарий на 4 части, одинаковые персонажи/гардероб/время/цвет/оптика, начало Seedance от фото владельца; три следующих `start` — точные последние кадры предшествующих частей. Сравни три пары edge frames и лица/предметы вручную; при резком скачке перегенерируй сегмент в новом проекте. Отдельный soundtrack на всю полосу 30 с помогает скрыть аудиостык; право на исходный звук проверь. FFmpeg нормализует геометрию и длительность, но не чинит художественную непрерывность.

## Прогон завтра на машине владельца

Владелец даст фотографии, историю, а затем ключ OpenRouter; сейчас их здесь нет. Ключ сохрани только существующим секретным способом Bossman; ни фото, ни историю, ни ключ, ни рендеры в git. Запрос на внешнюю передачу фотографий и стоимость выполняй в существующем разрешительном механизме. Подставь реальные пути и используй пустую директорию вне репозитория:

```bash
python tools/bossfield_owner_run.py init --project /private/bossfield-001 --story /private/story.txt --photo /private/photo1.jpg --aspect 9:16
python tools/bossfield_owner_run.py preflight --project /private/bossfield-001
```

Через существующий Bossman Terminal Run / Studio с тем же backend сделай cloud-задание `openrouter:bytedance/seedance-2.5` duration=15, resolution=720p, aspect_ratio=9:16, image role=`start` из `references/owner-01.jpg`; перенеси **проверенный** provider output в `raw/cloud.mp4` и приложи Studio receipt/provenance в приватный audit. После каждого локального сегмента извлекай опорный кадр:

```bash
python tools/bossfield_owner_run.py anchor --project /private/bossfield-001 --after cloud
# Studio sdcpp:wan2.2-ti2v-5b: start=references/anchor-after-cloud.png, frames=81, fps=16, output=raw/local_01.mp4
python tools/bossfield_owner_run.py anchor --project /private/bossfield-001 --after local_01
# start=references/anchor-after-local_01.png → raw/local_02.mp4
python tools/bossfield_owner_run.py anchor --project /private/bossfield-001 --after local_02
# start=references/anchor-after-local_02.png → raw/local_03.mp4
python tools/bossfield_owner_run.py assemble --project /private/bossfield-001 --music /private/bossfield-001/soundtrack.wav
python tools/bossfield_owner_run.py verify --project /private/bossfield-001
```

Музыка опциональна; без неё результат будет без звука. `assemble` требует реальные четыре клипа с достаточной длительностью и аспектом, делает 720×1280/1280×720 H.264 24 fps ровно 30 с, полный decode, SHA и 3 пары кадров; статусы `ASSEMBLED_PENDING_REVIEW`/`EVIDENCE_INTACT_PENDING_VISUAL_REVIEW` — это НЕ принятие работы, не проверка происхождения провайдера и не визуальное PASS. Следующий шаг: независимый зритель проверяет динамику, идентичность, сочетаемость, права и удовольствие от результата. Если 15-секундный cloud provider выдаёт >15 с, монтаж обрежет; если меньше 15 с — остановится, не дорисовывает фальшивые секунды. Режимы `init/preflight/anchor/assemble/verify` доступны как технический инструмент и должны работать через один продукт при интеграции terminal surface.

## Skill-кандидат и цикл улучшения

Создай кандидат в существующей библиотеке skills через Bossman CLI/backend, scope: `owner_photo_story_to_30s_video`, вход owner story + image roles + разрешённые бюджет/провайдер, выход видео + receipt + визуальный review, стоп-сигнал для недостаточной длительности/денег/VRAM/сходства. Версионируй промпты, порог seam review, сэмплы и cost/time; сравни с baseline на новой истории без доступа ученика к ответам; независимый evaluator и regression решают promote/reject. Повторение того же ролика после ручного исправления Claude самообучением не считается. Фиксируй 2–3 контрастных сценария, реальные оценки зрителя, стоимость секунды и время до принятого видео. При улучшении кандидат становится версионированным skill через существующий gate, не тихой правкой stable.

## Первоисточники и ограничения

- [OpenRouter Seedance 2.5](https://openrouter.ai/bytedance/seedance-2.5) — 4–30 с, 720p и референсы; цены плавают и зависят от режима.
- [OpenRouter Seedance 2.0](https://openrouter.ai/bytedance/seedance-2.0) — 4–15 с, standard fallback только после явной проверки.
- [OpenRouter Video Generation](https://openrouter.ai/docs/guides/overview/multimodal/video-generation) — асинхронный API `/videos`, `frame_images`, `input_references`; запрос через governable Studio.
- [LTX-Desktop README](https://github.com/Lightricks/LTX-Desktop#readme) — локальный путь Windows/Linux требует CUDA; AMD API-only.
- [Official Higgsfield GitHub](https://github.com/higgsfield-ai) — идеи CLI/SDK/skills; нет оснований считать весь сервис открытым или обещать награду за форк. Старые заметки `docs/v8/OWNER_IDEA_OPEN_HIGGSFIELD.md` и `docs/v8/FORK_CONTEST_BRIEF_RU.md` — идеи, не подтверждённый лицензионный договор.
