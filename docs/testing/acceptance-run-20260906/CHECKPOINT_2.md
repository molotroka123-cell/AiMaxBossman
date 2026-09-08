# CHECKPOINT 2 — acceptance-20260906-01 (live operator run: fixes chain + unload)

Дата: 2026-09-06 ~23:30 CEST. RUN_ID=acceptance-20260906-01 (продолжение).

## Идентификация

- TESTED_CODE_SHA (runtime последней версии) = 60efe77 (ветка acceptance/total-local-20260906)
- Цепочка исправлений живого прогона: a4da0f8 (AT-01/AT-03/parking) → 66bfef2 (planner max_tokens) → 60efe77 (planner JSON schema)
- REPORT_COMMIT_SHA = этот коммит
- ACTUAL_MODEL_ID = z-ai/glm-5.3-flash через OpenRouter (ключ владельца; ПЕРВЫЙ ключ истёк посреди прогона → выдан второй; ключи хранятся ТОЛЬКО вне репозитория, в коммиты не попадают, секрет-скан перед пушем)
- OWNER_SESSION_MATCH = session 1, интерактивная сессия владельца (наблюдение UIA видело реальный рабочий стол)

## Что произошло в живом прогоне Сценария A (Проводник+Блокнот) — цепочка вскрытых дефектов

Каждый шаг подтверждён логами gateway/core и репродукцией:

1. **GATEWAY-URL-V1 (P2, open)**: BOSSMAN_GATEWAY_URL обязан содержать `/v1` (дефолт `http://127.0.0.1:8765/v1`). Мой первый запуск без `/v1` → каждый ход планировщика 404 → 21 мгновенный replan → FAILED «planner replan budget». Клиент склеивает base+`/chat/completions` (gateway/client.py:55,135). Рекомендация: валидация/предупреждение при старте Core.
2. **OBSERVER-DEPS-001 (P1, локально компенсировано, repo-fix open)**: Windows-адаптер оператора зависит от pywinauto/pywin32/pyautogui/Pillow, но НИ ОДИН не объявлен в bossman-core/pyproject.toml (нет extra), и bossman_doctor их не проверяет → на чистой машине оператор «слепой»: наблюдение возвращает `error: ModuleNotFoundError`, планировщик выжигает replan-бюджет, задача FAILED. Локально: `pip install pywinauto pyautogui pywin32 pillow` в изолированный venv → наблюдение увидело РЕАЛЬНЫЙ рабочий стол (foreground-заголовок, UIA-дерево, скриншот). Нужный repo-фикс: extra `windows` в pyproject + check в doctor.
3. **PLANNER-TOKENS-001 (FIXED 66bfef2)**: max_tokens=1200 у планировщика; z-ai/glm-5.3-flash — reasoning-модель: ~1200 токенов уходят на reasoning до начала content → content пустой → parse-ошибка каждый ход. Cap поднят до 4000 (replay-тест: finish=stop, content валидный JSON).
4. **PLANNER-SCHEMA-001 (FIXED 60efe77)**: системный промпт планировщика НЕ задавал JSON-схему ответа. GLM 5.3 возвращал осмысленный план, но с `args` строкой и постусловием-строкой → `ValueError: args must be object` каждый ход. Фикс: явная схема в PLAN_SYSTEM (kind/target/text/args-as-object/expected{...}) + толерантность парсера (строка args → {"raw": ...}).
5. **KEY-EXPIRY (env)**: первый OpenRouter-ключ владельца истёк посреди прогона → мгновенные 401 → 21 replan → FAILED. Выдан новый ключ (вне репо). Это подтверждает находку OPERATOR-OBSERVABILITY-001.
6. **OPERATOR-OBSERVABILITY-001 (P2, open)**: исключение планировщика не сохраняется в задаче — last_error содержит только «planner replan budget», реальная причина (404/401/parse) теряется. Диагностика живого прогона вслепую. Минимальный фикс: писать последнюю planner-ошибку в last_error/событие при каждом replan.
7. **PRIVACY (P2, note)**: наблюдения оператора пишут сырые заголовки окон владельца (личная страница Edge попала в tasks.json) и скриншоты всего экрана в %TEMP%\bossman-computer. Для приёмки это локальные данные (не публикую), но продукту нужна редакция foreground-заголовков перед сохранением/показом.

## Позитивное живое evidence

- Наблюдение: UIA-обход + скриншот РЕАЛЬНОГО рабочего стола владельца работают (foreground app/title/handle, ui_tree=available).
- Планировщик GLM 5.3-flash по реальному наблюдению построил корректный многошаговый план сценария A (explorer → новая папка → notepad → ввод маркера → Ctrl+S → сохранение по пути → переоткрытие), включая приватно-осознанное решение «не кликать по личной странице Edge». Маршрут проверен по факту вызовов (gateway telemetry: model=z-ai/glm-5.3-flash, outcome=ok).
- Пайплайн целиком подключён и запускается: Core→Gateway→OpenRouter→UIA→pyautogui.

## Состояние прогона

- Сценарий A: НЕ ЗАВЕРШЁН (end-to-end) — прерван истечением ключа; после выдачи нового ключа runtime по требованию владельца ПОЛНОСТЬЮ выгружен.
- Машина разгружена: остановлены bossman-core, bossman-gateway, postgres-контейнер (данные сохранены, контейнер bossman-pg-acceptance можно запустить), Docker Desktop, WSL VM, Ollama. RAM свободно: 4.0 → 5.81 GB.
- Возобновление прогона (когда владелец разрешит): docker start bossman-pg-acceptance → Docker Desktop → gateway (env из C:\bossman-acceptance\20260906T1945-d1e851b\runtime) → core → задача A заново.

## Регресс на TESTED_CODE_SHA (61 planner/operator тест) — зелёный после 60efe77.

## Next steps (по приоритету)

1. OPERATOR-OBSERVABILITY-001 фикс + регресс (маленький).
2. OBSERVER-DEPS-001 repo-фикс: `[project.optional-dependencies] windows` + doctor check.
3. Возобновить Сценарий A → B → C; затем D/E через CC UI; скорость и OWNER CONTROL сценарии.
