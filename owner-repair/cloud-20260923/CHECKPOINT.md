# CLOUD_PREPARE — чекпоинт облачного этапа 2026-09-23

Ветка: `claude/bossman-cloud-closure-owner-a6s1ki` → PR #74 → `integrate/owner-final-20260922` (PR #73) → `release/bossman-owner`.
Основа: `d6e25fb4` (integrate не продвигался после checkpoint). Статус этапа: **IN_PROGRESS** (не READY_FOR_OWNER_RUN, не BLOCKED).
Компьютер владельца не запускался. OWNER_HARDWARE_CERTIFIED не заявляется.

## Слито в ветку
| Коммит | Что | Проверка |
|---|---|---|
| ca53461e | Локальный sidecar `bossman.openhands.v1`, handshake-готовность, профили агентов, recall/рецепты, `verify_tests`, UNKNOWN_INTERRUPTED, proc_tree | apprentice 128 passed; cc coding 13 passed |
| 33f3fb9b | `coding_path_owner.py` из архива + шаг Windows CI; фикс кавычек в команде sidecar на Windows | локально BOSSMAN_CODING_PATH=PASS |
| f3150163 | Модельные профили + загрузчик (reuse/resume/STOP/диск/хэши/pin) | 47 passed; хэши UNPINNED — HF заблокирован сетью (403) |
| f25d6fd4 | Поставка coding_path_owner/model_fetch/model_profiles в app-support; UTF-8 консоль | тесты сборщика 242 passed |

## Сверка веток (запрос владельца)
* PR #71 (`claude/bossman-1-0-rc-owner-ready-cfesui` @ 737a31b1) — целиком в нашей линии.
* `integrate/1.0-20260922`: OpenCode v2 (3e71e08f) и Edge (4f5d5745) уже перенесены (2ce10cbe, 8df57439).
  MEDIA-RESTART (e2183fc3) и R6 stop-epoch (d22c3095) были отложены на заморозке (c3010bda) — сейчас переносятся на текущий дизайн.

## В работе
MVČR HW-10 · Windows-дефекты (OS-105, publish 500, SQLite lock) · агенты RAW…USER_UX + рецепты памяти ·
Owner-Run `self-improve-mvcr` + OWNER_RUN_NEXT.md · перенос MEDIA-RESTART/R6 · 8 исследовательских линий (OCR, модели, датасеты, скилы) ·
полный прогон command-center.

## Открыто
* root-ci: тест задержки записи красный на общем раннере (67/25/20 мс) — не разобран.
* Windows CI для SHA этой ветки — ждём.
