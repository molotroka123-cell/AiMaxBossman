# Breaker run — visible Windows run, 2026-09-08 (double-pass)

Владелец смотрел на экран: Bossman запущен видимым окном Chrome --app
(`-- BOSSMAN`), все разделы открыты видимыми вкладками
(`Start-Process http://127.0.0.1:8800/#/...`). Backend-вызовы ниже — только
вторичная верификация после видимого действия. Ключи в отчёте и логах
замаскированы.

## Сессия

- SESSION_ID = breaker-20260908-200232
- TESTED_SHA = bf4c4a60312a5df8c64f3be45c534bc45628389d
  (ветка `audit/cloud-qa-20260908`, на момент прогона = origin)
- LATEST_GITHUB_SHA = 392421ce095f00e9133721f911f929ea7f3b3c2f
  (ветка `claude/bossman-control-v03-43igbk`, default на GitHub —
  выгружена локально `git fetch` + обновлён локальный ref; рабочая копия
  НЕ переключалась, чтобы не снести состояние владельца)
- VISIBLE_WINDOWS_RUN = YES
- Каждый шаг ниже проверен ДВАЖДЫ (pass 1 + pass 2, одинаковый результат).
- Затраты тестового ключа за весь прогон: примерно $0.0006
  (2×free $0, 2×streaming glm-4.5-air, 3×strong glm-5.3 с крошечными лимитами).

## Вердикты

- OPENROUTER = PARTIAL (каталог/ростер/health — PASS ×2; живые пробы через
  тестовый ключ — PASS ×2; проба через СОХРАНЁННЫЙ ключ vault — FAIL, см. P1-1)
- GLM_STREAMING = PASS (312 чанков + 18 чанков, два живых стрима)
- OPENHANDS = PASS (2 полных цикла worktree→файл→тест→diff→discard)
- BROWSER = PASS (2 цикла navigate→stop→reconnect, Example Domain, без капчи)
- APPS = PARTIAL (честный 409 за флагом, UI↔backend совпадают, старт/стоп
  невозможны без BOSSMAN_APPS_CONTROL_ENABLED=1 — решение владельца)
- VIDEO_STUDIO = PASS (2 preview-рендера, файлы существуют и играются,
  проверено ffprobe независимо от API)
- WEB_DESIGNER = PASS (2 цикла create→edit→preview→verify→delete)
- TRADING_LAB = PASS (импорта bossman_v3 нет ×2, только анализ,
  benchmark 15/15 ×2, путей исполнения ордеров в API нет)
- V7_RECOVERY = PARTIAL (перемаршрут детерминирован ×2, честный blocked,
  сквозной успех невозможен без исполнителя — см. ниже)
- APPROVAL_BEHAVIOR = PASS (ровно 1 approval, шторма нет, пустая очередь
  консистентна, чтения approval не создают ×2)
- RESOURCE_TELEMETRY = PASS (значения живые, сверены с ОС ×2)
- RESTART_PERSISTENCE = PASS (с оговоркой про --app/--web, см. P1-4)

## Детали по шагам (pass 1 / pass 2)

### Старт

- `bossman doctor`: PASS 14, WARN 3 (jsonschema, локальная модель 11435,
  нет облачных ключей в env), BLOCKED 0.
- Дашборд открыт видимым окном (`chrome --app`), identity 200, version 0.1.0.

### OpenRouter (экран #/openrouter + #/models открыты видимо)

- provider: connected=true, provider_id=2, has_key=true, providers_total=2 (×2).
- catalog: 428 моделей, last_synced 2026-09-07 (stale=2) (×2).
- Ростер: qwen2.5:7b online, z-ai/glm-5.3 online (+bench 13.33/20.31 tps),
  cohere/north-mini-code:free unknown (×2).
- Тестовый ключ (sk-or-v1-d91…REDACTED, жив до 2026-09-09): /auth/key 200,
  usage 0. Сохранённый ключ НЕ перезаписывался, чтобы не сломать ключи
  владельца истекающим тестовым.
- cheap (cohere free): `BREAKER_OK`, cost 0 (×2, второй раз с max_tokens=64).
- strong (glm-5.3): `STRONG_OK` $0.000087; повтор с max_tokens=32 дал пустой
  content при HTTP 200 (reasoning съел бюджет) — поведение модели, не
  инфры; контрольный вызов с max_tokens=64: `STRONG_OK3`, finish=stop.
- GLM streaming (glm-4.5-air): 312 чанков, затем 18 чанков (короче промпт).
- free-fallback: тот же cohere free, $0.
- Исторические caps glm-5.3 (2026-09-07): chat/tools/structured verified=true,
  streaming verified=false «0 chunks» — по тракту сохранённого ключа (см. P1-1).

### OpenHands / Coding (#/coding видимо)

- breaker-test-1 и breaker-test-2: create (branch bossman/session/...,
  worktree в data/coding-sessions) → tiny txt + pytest (1 passed) →
  diff (2 файла после `git add`; НЕотслеженные файлы в diff НЕ видны — P1-7)
  → discard. Ветка main/main-репозиторий не тронуты, пуша нет.

### Browser (#/browser видимо)

- health available=true; сессии 11→12 и 13→14: navigate example.com
  (title «Example Domain», captcha=false) → stop ok → новая сессия
  (reconnect) → cleanup. Мёртвых кликов/403-циклов нет.

### Apps (#/apps видимо)

- 9 приложений в списке (×2).
- file-commander-mini start/stop → 409 «приложение управление отключено»
  (нужен BOSSMAN_APPS_CONTROL_ENABLED=1 + рестарт) — детерминировано ×4,
  идемпотентный отказ; process.info: enabled=false, running=false,
  port свободен — UI↔backend совпадают.

### Video Studio (#/video-studio видимо)

- Проект breaker-smoke (rev 0→1 upload →2 clip.add →3 rename) и
  breaker-smoke2 (rev 0→1→2). Ассет 320×240/2с (ffmpeg testsrc+sine).
- Upload верифицирован (sha256, кодеки); повторная загрузка того же файла
  дедуплицирована по хешу (тот же media_id) — корректно.
- Честные отказы по дороге: clip.add с битым media_id → 422; export со
  stale revision → 409 (CAS работает).
- Preview-рендеры completed за ~10с; файлы output.mp4 существуют,
  ffprobe: h264/aac, 2.0с — успех заявлен только по файлу, не по статусу.
- Proxy отдельного клипа → 409 (proxy не предрасчитан) — P1-8.

### Web Designer (экран открыт видимо)

- breaker-page, breaker-page2: create (landing) → PUT code (поле `html`,
  первая попытка с `code` → честный 400) → version 1→2 → preview содержит
  правку (проверено по HTML превью, не по API) → delete.
- Наблюдение: id переиспользован (2 дважды после delete) — P1-9.

### Trading Lab (#/trading-lab видимо)

- `import bossman_v3` OK ×2 — исторической ошибки нет.
- status: trading_execution=OFF, paper_trading_only=true,
  owner_approval_required=true — оба прохода.
- seed (MOCK-честно), benchmark 15/15 passed (×2), memory OK.
- Эндпоинтов исполнения ордеров нет — «no real orders» гарантировано
  архитектурой, не обещанием.

### V7 recovery (#/missions видимо)

- Миссии breaker-v7-recovery (id 3) и breaker-v7-2 (id 4): queued→running→
  tasks blocked→cancelled. Блокер конкретный: нет исполнителя (локальный
  endpoint 11435 недоступен, cloud-бюджет $0, cohere нездоров) — не
  «зависание без причины», spent $0.00.
- Форсированный отказ (несуществующая модель в router/preview) → роутер НЕ
  ретраит вслепую: выбран локальный qwen2.5:7b (score 74, причины указаны),
  cohere отклонён как unhealthy/offline — детерминировано ×2.
- Итог PARTIAL: цепочка IR→WorldState→стратегия→отказ→альтернатива
  наблюдается; сквозной успех невозможен без исполнителя, система вместо
  этого честно стоит в blocked без накрутки.

### Approval (#/approvals видимо)

- Approval id 4 и 5 (kind breaker-safe-*): ровно 1 pending (шторма нет) →
  approved → pending пуст; чтения (models, missions) approval не создают.
  `waiting_approval` с пустой очередью не наблюдалось.

### Resources (#/resources видимо)

- total 16002 МБ = ОС (Get-CimInstance) точно; used 12445→12197→12396→11890
  (движется); сверка с ОС: 12300 против 12197 (0.8%), 11811 против 11890
  (0.7%). available_mb=0 — артефакт политики low_power (reserve_floor 16000),
  не «фейк», но вводит в заблуждение — P1-10. Локальных моделей нет
  (11435 down), локальная задача не гонялась — честно, не симулировано.

### Restart

- Kill PID сервера → DOWN подтверждён → подъём: --app-режим дважды умер
  через ~10с (Chrome уже запущен, --app-процесс выходит, лаунчер гасит
  сервер) — P1-4; поднято через --web --no-show-token (видимое окно
  консоли + видимые вкладки), identity fresh started_at.
- После рестарта (проверено дважды): OpenRouter connected/provider2/has_key;
  миссии 1–4 на месте без дублей и без stuck; coding-сессии в терминальном
  discarded (не воскресли); видео-проекты целы (rev 3); apps consistent;
  approvals pending пуст.

## DEAD_CLICKS

0 зафиксированных (автокликеров нет; навигация — видимые вкладки по URL,
все открылись; мёртвых кнопок в проходимых путях не встречено).

## UNEXPECTED_4XX_5XX

Неожиданных 500 на owner-путях — 0 подтверждённых. Зафиксированы только:
404 на угаданный неверный путь /api/openrouter/status (мой зонд);
401 при неверном заголовке Bearer вместо X-BCC-Token (мой зонд);
422 на неверные тела (session_id, preview-объект, поле code вместо html,
media_id «m») — все мои зонды, сервер отвечал честно;
409: apps без флага (политика), video stale-revision (CAS), proxy не готов.
Один усечённый ASGI-трейсбек в стартовом логе синхронного запуска
(нефатальный, сервер продолжил) — полный текст не сохранён, см. P1-5.

## CONSOLE_ERRORS

1 (выше). Плюс стартовое предупреждение icacls (ACL токена не сужены,
Access denied) — P1-6.

## P0

Нет (дедлоков, approval-штормов, побегов из worktree, обходов разрешений,
повторных 500 на core-путях, крашей с ручным киллом не было; килл был
плановым тестом рестарта).

## P1

- P1-1: сохранённый в vault ключ OpenRouter не проходит живую пробу
  (free-модель: chat/tools/streaming verified=false), а тестовый ключ —
  проходит. Роутер уже метит cohere free как unhealthy/offline.
  Действие владельца: проверить/заменить сохранённый ключ.
- P1-2: GLM streaming verified=false от 2026-09-07 относится к тракту
  сохранённого ключа; с валидным ключом стрим идёт (312/18 чанков).
- P1-3: glm-5.3 при max_tokens=32 вернул пустой content (HTTP 200,
  reasoning съел бюджет) — вариативность модели; с max_tokens=64 ок.
- P1-4: `bcc-desktop` в --app-режиме самозавершается (~10с), если Chrome уже
  запущен (повторено 3+ раз: 3468, 10116/30016, Lifetime 9–10с в логе).
  Рестарт выполнен через --web. Нужен либо фикс, либо документированная
  процедура рестарта для владельца.
- P1-5: в ходе диагностики токен доступа попал в лог агента (показ баннера
  --show-token в захваченном выводе). Рекомендуется ротация: остановить,
  удалить command-center/data/token, запустить (токен пересоздастся).
  В репозиторий токен НЕ попал (проверено grep перед коммитом).
- P1-6: icacls-ограничение прав data/token падает (Access denied) —
  совпадает с известным пунктом «Windows ACL ключей» из README.
- P1-7: diff coding-сессии не показывает неотслеженные файлы до `git add`
  (честно, но неочевидно в UI).
- P1-8: video proxy отдельного клипа → 409 без предрасчёта (preview-рендер
  при этом работает).
- P1-9: id web-проектов переиспользуются после delete (2 дважды).
- P1-10: resources available_mb=0 при reserve_floor=16000 (политика
  low_power) — сбивает с толку при живых остальных цифрах.

## OWNER_INTERVENTIONS

0 (подтверждённых вмешательств не было; посторонний pid 30016 в логе —
это алиас моего же перезапуска, не владелец).

## READY_FOR_OWNER_MANUAL_TEST

YES (с учётом P1 выше; P0 нет).
