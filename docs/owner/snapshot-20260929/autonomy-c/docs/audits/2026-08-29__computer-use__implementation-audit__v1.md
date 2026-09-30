# ComputerUse V1.2 — аудит интеграции

**Дата:** 2026-08-29
**Ветка:** claude/bossman-control-v03-43igbk
**База интеграции:** `bossman-core` (ТЗ v0.3), а НЕ `command-center`.

## Решение о размещении

В репозитории два ядра. `command-center/bcc` (дашборд V2.2) уже имеет
собственный, более продвинутый браузерный слой — канареечно проверенный на
утечку пароля (`docs/audit/OPEN_ITEMS_VERIFICATION.md`), — и он НЕ тронут.
`bossman-core` (ТЗ v0.3) браузерного инструмента не имел вовсе, и ZIP написан
ровно под его реестр (`from . import ToolDef, register`). Поэтому ComputerUse
интегрирован в `bossman-core`: это не дубль архитектуры, а закрытие реального
пробела штатными средствами ядра.

Принцип REUSE → ADAPT → WRITE NEW соблюдён: код браузера из ZIP взят как есть
(657 строк), адаптации не потребовалось — сигнатура реестра совпала.

## Файлы

| Файл | Что сделано |
|---|---|
| `bossman/toolkit/browser.py` | новый — 22 инструмента `browser.*` из ZIP |
| `bossman/toolkit/__init__.py` | `browser` добавлен в регистрацию модулей |
| `bossman/agents.py` | `BASE_COMPUTER_USE_TOOLS` + `_merge_computer_use_tools`; `computer_use` включён по умолчанию, опт-аут `computer_use: false`; ручная настройка агента побеждает дефолт |
| `bossman/api.py` | `BrowserManager.shutdown()` подключён к shutdown сервиса — нет осиротевших Chromium |
| `pyproject.toml` | зависимость `playwright>=1.47` |
| `.gitignore` | профили, cookies и загрузки браузера исключены |
| тесты | policy (6) + emulator E2E (2) + approval/injection (4) |
| docs | AUDIT_PROTOCOL, EMULATOR_BUGCHECK, GITHUB_REPO_HYGIENE, COMPUTER_USE_OPERATIONS |

## Security — проверено, не задокументировано

- **submit не обходит approval.** Обычный `browser.press` отвергает
  Enter/Ctrl/Meta/Shift+Enter и NumpadEnter — только `browser.confirmed_press`.
  Consequential-инструменты (`confirmed_click`, `confirmed_press`) несут
  `confirm=True`; runner паркует такую задачу в `waiting_approval` и ждёт
  человека (`runner.py:87-96`). Доказано тестом.
- **Без обхода защиты.** `BLOCKERS` (captcha, rate limit, bot detected,
  unusual traffic, access denied) → остановка, не обход. Обхода капчи/антибота
  в коде нет.
- **Страница — недоверенные данные.** `browser.extract` и vision-bundle
  помечают содержимое как untrusted.
- **Изоляция.** Отдельный постоянный профиль Chromium на агента, кросс-процессный
  замок профиля.

## Эмулятор — обязательный гейт

Прогон на НАСТОЯЩЕМ Chromium (`/opt/pw-browsers/chromium-1194`, через
`BOSSMAN_TEST_CHROMIUM`): `tests/test_browser_emulator_e2e.py` — **2 passed**.

Найден один дефект гейта: тест персистентности профиля проверял сессионную
cookie (без `expires`), которую Chromium на диск не пишет вовсе — падение было
на условии теста, не на коде. Проверено эмпирически (персистентная cookie
переживает рестарт, сессионная — нет), тест исправлен на персистентную cookie.

## Тесты

`cd bossman-core && python -m pytest -q` → **37 passed** (было 25).
Прибавка 12 = 8 browser (policy+emulator) + 4 approval/injection.

## Ограничения / NOT RUN

- Полноценный vision-инференс не проверялся: `browser.vision` собирает
  мультимодальный bundle (PNG + DOM + метаданные), но реальная отправка в
  vision-модель зависит от LLM-адаптера конкретного агента. Bundle готов,
  инференс — NOT RUN.
- SIGTERM-хук за пределами FastAPI shutdown не добавлялся: у `bossman-core`
  один сервис на uvicorn, его `on_event("shutdown")` покрывает штатную
  остановку.

## Финальный вердикт

**COMPUTER USE STAGE 1 — PASS.** Эмулятор-гейт зелёный на настоящем Chromium,
approval-гейт доказан, обхода защиты нет, профили в git не попадают.

Точный SHA будет проставлен коммитом этой правки.
