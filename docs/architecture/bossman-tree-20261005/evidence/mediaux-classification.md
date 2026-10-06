# mediaux: классификация листьев (зоны media + ux)

GREEN 66 · RETIRE 1 · KEEP 1 · всего 68

PASS = импорт в чистом подпроцессе с этим worktree первым в PYTHONPATH (assert __file__ внутри) и >=1 тест, все прошли. Если у теста нет браузера (Chromium недоступен), такие упавшие по среде тесты исключены и отмечены `env_excluded=N` в probe; продуктовые падения не исключаются.

| id | label | verdict | reason | value |
|---|---|---|---|---|
| cap-12 | Чат · история · streaming SSE | GREEN | import+pytest: import из этого worktree + 25 тестов прошло; TOP: чат и история владельца: основной канал управления | TOP |
| cap-13 | CMD и пользовательские команды | GREEN | import+pytest: import из этого worktree + 74 тестов прошло; TOP: CMD: терминальный клиент Bossman | TOP |
| cap-29 | Motion Studio | GREEN | import+pytest: import из этого worktree + 38 тестов прошло | OK |
| cap-30 | Video · Music · Images Studio | GREEN | import+pytest env_excluded=2: import из этого worktree + 57 тестов прошло | OK |
| mod-game_bootstrap_v16 | game_bootstrap_v16 | GREEN | import+pytest: import из этого worktree + 5 тестов прошло | LOW |
| mod-images | images | GREEN | import+pytest: import из этого worktree + 46 тестов прошло | OK |
| mod-motion_studio | motion_studio | GREEN | import+pytest: import из этого worktree + 38 тестов прошло | OK |
| mod-music_studio | music_studio | GREEN | import+pytest: import из этого worktree + 31 тестов прошло | OK |
| mod-studio | studio | GREEN | import+pytest env_excluded=2: import из этого worktree + 66 тестов прошло | OK |
| mod-studio_review | studio_review | GREEN | import+pytest: import из этого worktree + 28 тестов прошло | OK |
| mod-video_studio | video_studio | GREEN | import+pytest: import из этого worktree + 94 тестов прошло | OK |
| app-social-farm | social-farm | GREEN | import+pytest: import из этого worktree + 429 тестов прошло | OK |
| slash-help | /help | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло | LOW |
| slash-status | /status | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло | OK |
| slash-tasks | /tasks [N] | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло | OK |
| slash-models | /models [use <id/alias>] | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло; TOP: выбор модели агента (маршрутизация бесплатных моделей) | TOP |
| slash-agent | /agent [<id/имя>] | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло | OK |
| slash-skills | /skills [запрос] | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло | OK |
| slash-tools | /tools | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло | OK |
| slash-memory | /memory <запрос> | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло; TOP: поиск по памяти и фактам | TOP |
| slash-diff | /diff [coding-task-id] | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло | OK |
| slash-code | /code --allow <путь> [--verify <тест>] <задача> | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло | OK |
| slash-rave | /rave "<задача>" --agents a,b / status/pause/resume/stop/diff <id> | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло | OK |
| slash-approve | /approve [id] | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло; TOP: одобрения: ни одно действие без решения владельца | TOP |
| slash-deny | /deny [id] | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло; TOP: одобрения: отказ без побочных эффектов | TOP |
| slash-approvals | /approvals | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло | OK |
| slash-pause | /pause [task] | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло | OK |
| slash-stop | /stop [task/all] | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло; TOP: глобальный STOP и остановка задач (надёжность) | TOP |
| slash-resume | /resume [task] | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло | OK |
| slash-computer | /computer [status/stop/resume] | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло | OK |
| slash-evolve | /evolve [status/pause/resume/stop/report] | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло; TOP: управление циклом самоулучшения | TOP |
| slash-keys | /keys [set <vendor>/remove <vendor>/import-env] | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло; TOP: ключи: секрет не принимается из командной строки | TOP |
| slash-panel | /panel | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло | OK |
| slash-compact | /compact [инструкция] | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло | OK |
| slash-context | /context | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло | OK |
| slash-cost | /cost | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло | OK |
| slash-export | /export [путь] [--force] | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло | OK |
| slash-doctor | /doctor | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло | OK |
| slash-expand | /expand [N] | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло | LOW |
| slash-history | /history [on/off] | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло | LOW |
| slash-clear | /clear | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло | LOW |
| slash-exit | /exit | GREEN | import+pytest authored_by_lane: import из этого worktree + 1 тестов прошло | LOW |
| reg-video_studio_render | Video Studio: компилятор filtergraph (preview и export) | GREEN | import+pytest: import из этого worktree + 46 тестов прошло | OK |
| reg-video_studio_service | Video Studio: интеграция с хостом (БД, задачи, команды) | GREEN | import+pytest: import из этого worktree + 35 тестов прошло | OK |
| reg-video_studio_storyboard | Video Studio: раскадровка и рецепты кадров | GREEN | import+pytest: import из этого worktree + 13 тестов прошло | OK |
| reg-video_studio_trained | Video Studio: черновик команд от модели + валидация | GREEN | import+pytest: import из этого worktree + 30 тестов прошло | OK |
| reg-video_studio_shotcut | Video Studio: обмен проектами Shotcut/MLT | GREEN | import+pytest: import из этого worktree + 2 тестов прошло | OK |
| reg-video_studio_interchange | Video Studio: обмен OpenTimelineIO | GREEN | import+pytest: import из этого worktree + 3 тестов прошло | OK |
| reg-video_studio_analysis | Video Studio: диагностика, тайминг субтитров, ASR | GREEN | import+pytest: import из этого worktree + 47 тестов прошло | OK |
| reg-video_studio_retrieval | Video Studio: поиск B-roll и дубликатов | GREEN | import+pytest: import из этого worktree + 7 тестов прошло | OK |
| reg-video_adapter_train | Video Studio: обучение LoRA-адаптера команд | GREEN | import+pytest: import из этого worktree + 1 тестов прошло | OK |
| reg-studio_provider | Studio: жизненный цикл провайдеров | GREEN | import+pytest: import из этого worktree + 78 тестов прошло | OK |
| reg-studio_sdcpp | Studio: локальная генерация stable-diffusion.cpp | GREEN | import+pytest: import из этого worktree + 66 тестов прошло | OK |
| reg-studio_comfyui | Studio: провайдер ComfyUI | GREEN | import+pytest: import из этого worktree + 34 тестов прошло | OK |
| reg-studio_openrouter | Studio: провайдер OpenRouter (изображения и видео) | GREEN | import+pytest: import из этого worktree + 58 тестов прошло | OK |
| reg-studio_governance | Studio: бюджетные резервы по функциям | GREEN | import+pytest: import из этого worktree + 47 тестов прошло; TOP: бюджетные резервы Studio (жёсткий потолок) | TOP |
| reg-studio_review | Studio: локальный просмотр сгенерированных видео | GREEN | import+pytest: import из этого worktree + 28 тестов прошло | OK |
| reg-oss_comfyui | ComfyUI: ограниченный локальный адаптер | GREEN | import+pytest: import из этого worktree + 68 тестов прошло | OK |
| reg-web_designer_gen | Web Designer: генерация сайтов | GREEN | import+pytest: import из этого worktree + 42 тестов прошло | OK |
| reg-web_designer_visual | Web Designer: визуальный редактор (версии) | GREEN | import+pytest: import из этого worktree + 13 тестов прошло | OK |
| reg-web_designer_ai_build | Web Designer: сборка с помощью ИИ | GREEN | import+pytest: import из этого worktree + 27 тестов прошло | OK |
| reg-ai3d_pipeline | 3D Maker: пайплайн от запроса до печатаемого артефакта | GREEN | import+pytest: import из этого worktree + 35 тестов прошло | OK |
| reg-ai3d_gcode | 3D Maker: сканер безопасности G-code | GREEN | import+pytest: import из этого worktree + 88 тестов прошло | OK |
| reg-social_farm_media | Social Farm: преобразование медиа без изменения исходника | GREEN | import+pytest: import из этого worktree + 6 тестов прошло | OK |
| reg-motion_make_video | Motion Studio: сцена в видео (озвучка, музыка) | KEEP | нужна среда: tools/motion_studio/make_video.py импортирует score (scipy), scipy не установлен в Python312 линии; модуль живой (запускается features/motion_studio.py), удалять нельзя, тестов без scipy нет | LOW |
| reg-motion_epic | Motion Studio: оффлайн-трейлер на Pillow | GREEN | import+pytest authored_by_lane: import из этого worktree + 5 тестов прошло | LOW |
| reg-genjutsu_tool | Genjutsu: локальная замена персонажа и перенос движения | GREEN | import+pytest: import из этого worktree + 3 тестов прошло | OK |
| reg-promo_video | Промо-видео: рендер с саундтреком | RETIRE | Одноразовый скрипт рендера промо '32 дня' (даты 2026-08-27..09-27 зашиты в promo.html): ни одного импортёра, нет маршрута/CLI/манифеста, в документации не заявлен как функция, требует Chromium и Kokoro; не безопасность/бюджет/бэкап. Выпадает из дерева, файл остаётся в git. | LOW |
