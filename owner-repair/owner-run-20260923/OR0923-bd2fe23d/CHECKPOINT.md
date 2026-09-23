# OWNER RUN 2026-09-23 — чекпоинт (RUN_ID OR0923-bd2fe23d)

Terminal Run 1.2 = другой пульт того же Bossman (общие файлы/память/задачи); цель — измеряемое самоулучшение и бизнес-пилот в окне 4–6 дней, не гарантированный заработок.
North Star: SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT (выше — не доказано в этом прогоне пока).

## Код и архив
- SOURCE_BRANCH `integrate/owner-final-20260922`, TESTED_SHA `bd2fe23dab55704d60ed468bcbb4664475e49879` (дерево = 3707d6ab, содержит PR #74; `release/bossman-owner` e0bf948d — предок, отстаёт).
- PACKAGING_BLOCKER: в GitHub Actions нет Windows ZIP ни для bd2fe23d, ни для 3707d6ab (последний — 68dded39). Собран локально штатным `tools/build_windows_bundle.py --zip --profile release` из чистого LF-клона: inputs LOCKED, `verify_windows_bundle.py` → `BOSSMAN_BUNDLE_ACCEPTANCE=PASS`.
- ZIP `BOSSMAN-Windows-x64-bd2fe23dab55.zip` SHA-256 `95476d4a560d9f347ca4797b75475dbd9e20c52417b209701f7692bf6ab3528c` (локальная сборка, не CI-сертификат).

## Среда
- Ryzen AI MAX+ 395 / Radeon 8060S / 119.6 GB. Сессия Claude Code и её дочерние процессы — с правами администратора.
- **ENVIRONMENT_BLOCKER: Smart App Control = On** блокирует неподписанные llama.cpp DLL (`0xC0E90002`). SAC не отключался (необратимо). Runtime заменён на подписанный Ollama 0.34.3 (отдельный экземпляр :11435, keep_alive=-1) + прокси :11500 `reasoning_effort=none` (аналог `--reasoning off`). Веса те же GGUF (импорт из локальных файлов, без загрузки).
- MAIN Qwen3.8-27B UD-Q5_K_M ctx 64k (~10 tok/s), FAST Qwen3.6-35B-A3B UD-Q5_K_M ctx 32k.
- Тестовая установка `C:\Users\asd\Bossman Test 0923\` (путь с пробелами), data root `...\data`, порт 8810. Рабочая установка/БД владельца не тронуты.

## Результаты на сейчас (факты)
- UI: вход, «Найти локальные» нашёл Ollama, модели и агенты созданы в окне; сразу видны в CLI.
- CLI из архива: help/status/list/exec/events/result/stop работают; stdout без ANSI и без токена.
- TR-10: повтор request_id → та же задача (replayed), replay по курсору без дублей; обрыв клиента → задача доживает, `events --follow` дочитывает.
- TR-11: закрытие окна Windows Terminal посреди задачи → задача завершилась в backend.
- TR-12: `stop` → STOPPED (exit 6); `stop --all` останавливает задачи и Computer Use; STOP Computer Use пережил рестарт backend; ожидавший approval аннулирован.
- TR-13: `approve` из окна учителя отклонён (NO_APPROVE); approval виден в UI «Ждут решения». Положительный approval — NOT_RUN (нужно решение владельца).
- TR-14: ESC/OSC52/BEL/RLO/CR в заголовке и ответе не доходят до stdout (JSON и текст).
- TR-20: мёртвая модель → повторы → подмена на другую локальную модель → PASS; облачная модель без цены заблокирована, с ценой — не выбрана.
- ConHost + Windows Terminal: кириллица без mojibake, Tab-дополнение, Ctrl+C = отмена с подтверждением backend, многострочная вставка правым кликом не исполняется построчно, resume сессии.

## Найденные дефекты (черновик)
- P1 чат: перенесённый контекст «Запомни …» на каждом следующем ходе включает action-contract → верный ответ = FAIL + новый approval memory.fact.add.
- P2: Ctrl+V не вставляет, Shift+Insert вставляет `[2;2~` в чате ConHost; после Ctrl+C `/exit` показывает «Terminate batch job (Y/N)?»; prompt чата выглядит как prompt cmd; `resume` не восстанавливает агента; 10 строк «проверка: NOT_APPLICABLE».
- P2: агент из UI получает `tools=[]` без способа выдать инструменты в UI/CLI; coding path настраивается только env `BOSSMAN_OPENHANDS_COMMAND` + рестарт; coding-задачи не применяются к проекту (нет apply) → общий файл CLI→UI через продукт не создаётся.
- P2: UI позволил удалить модель, используемую агентом, — агент молча остался без модели.
- P2: двухфазный submit — обрыв между draft и run оставляет draft до повторного подключения.
- P2 упаковка: .cmd в архиве с окончаниями CR CR LF (работают, но это ошибка генерации).
- Документация: TERMINAL.md говорит, что prompt_toolkit нет в архиве — он есть (3.0.52).
- `/api/evolution/status` есть и отвечает (пробел из handoff #74 закрыт как минимум для status).

## Дальше
Coaching-пилот 5 train + 5 holdout на MAIN идёт; затем coding self-repair через CLI, рестарт + transfer, GUI-vs-CLI пары, MVČR (синтетика), медиа.

---
# Чекпоинт 2 (~14:50Z)

## Coding path / самоулучшение — блокеры продукта
- **P1 CODING-CRLF-EVIDENCE (продукт):** на Windows с `core.autocrlf=true` (умолчание Git for Windows) coding path отказывает нетронутому репозиторию Bossman: «evidence mismatch: git hides changes» (CSV с CRLF в блобе). Регрессия падает до / проходит после; fix `fix/coding-path-crlf-blob-20260923` @7439917f (bossman-core/tests/apprentice: 132 passed, 18 skipped). В TESTED_SHA НЕ влит; на прогоне — обход окружения `GIT_CONFIG core.autocrlf=false`.
- **P1 функциональный пробел:** репозиторий Bossman 80 МБ > `_MAX_SNAPSHOT_BYTES` 32 МБ → «workspace evidence exceeds bounded snapshot size». «Bossman улучшает Bossman» на полном репо невозможно; для эксперимента — ограниченная lab-копия `tools/` того же SHA (отклонение записано в BENCHMARK_MANIFEST до действий ученика).
- P2: `bossman code` не принимает инструкцию из файла; перевод строки в аргументе обрезает вызов через bossman.cmd.

## Обучение
- Coaching-пакет 5+5 на MAIN: 10/10 без помощи, coached = unassisted = 1.0 → пакет насыщен, **NO_MEASURED_GAIN** (потолок), WEIGHTS_UNCHANGED.
- D1 (реальный дефект CR CR LF в .cmd архива), экзамен заморожен до попыток (hv_d1, D2 unseen запечатаны вне корней ученика).
  - попытка 1 L0: FAIL (max_steps 40, 7 мин) — цикл одинаковых поисков;
  - попытка 2 L1: FAIL (max_steps, 5.5 мин) — нашёл строку причины поиском, не воспользовался; 23 одинаковых вызова;
  - наблюдение: в local_sidecar нет детектора повторов.

## MVČR (HW-10) — синтетика + живые официальные источники
- Owner-Run.cmd self-improve-mvcr mvcr: GET только mv.gov.cz / ipc.gov.cz; форма «Tiskopis žádosti o vydání povolení k trvalému pobytu – občané 3. zemí» с provenance/sha256; хронология только из подтверждённых фактов (7 л. 6 мес.); разные суммы пошлины → вопрос владельцу, не выбор; итог PARTIAL_MISSING_DATA; ничего не подано/не подписано/не оплачено.

## Web Designer (UX в окне)
- заготовка → проект v1 → правка кода (маркер) → Ctrl+S/автосохранение → перезагрузка → маркер на месте, v2/v3 → «Вернуть v1» с подтверждением → v4 = v1, сохраняется после перезагрузки. P2: панель истории не обновляется до перезагрузки.
