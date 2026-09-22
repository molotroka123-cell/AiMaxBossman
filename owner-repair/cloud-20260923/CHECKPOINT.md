# CLOUD_PREPARE — чекпоинт облачного этапа 2026-09-23

Ветка: `claude/bossman-cloud-closure-owner-a6s1ki` → PR #74 → `integrate/owner-final-20260922` (PR #73) → `release/bossman-owner`.
Статус этапа: **IN_PROGRESS**. Компьютер владельца не запускался. OWNER_HARDWARE_CERTIFIED не заявляется.

## Путь в main (исправлено)
Ранний вывод «у main и release нет общей истории» был ОШИБКОЙ: локальный клон был shallow (`.git/shallow`).
После `git fetch --unshallow` измерено: `main` (799fc3dd) и `night/v7` (10653d91) — предки release и нашей линии;
`main..наша` = 951, обратно 0. Продвижение — обычная перемотка вперёд по цепочке PR:
#74 → #73 → release → #67 (release → night) → #58 (night → main), либо один PR release → main.
`--allow-unrelated-histories`, force-push и замена дерева не нужны.
Защита веток на GitHub выключена у всех веток; CODEOWNERS одинаковый; ветка по умолчанию — `claude/bossman-control-v03-43igbk`
(16 своих коммитов: торговые кейсы и CI, без продуктового кода). Владельческие Windows-задания на main не запускаются
(`tools/owner_facing_branches.json`) — сертификация точного SHA делается на release до слияния.
Решение о слиянии в main и включении защиты — за владельцем (MERGE_OWNER_REQUIRED).

## Черновые PR в release, не входящие в линию (кандидаты, не 1.0)
| PR | Что | Статус |
|---|---|---|
| #68 | GPT Image + NL-маршрутизация (6 коммитов) | нет в линии — решение владельца |
| #69 | документ Dashboard Next (1 коммит) | только документ |
| #70 | Viral VFX + Brain Observatory (3 коммита, +1354 строки) | нет в линии — решение владельца |

## Слито в ветку (этот этап)
Coding path (локальный sidecar, handshake, профили, recall/рецепты, verify_tests, UNKNOWN_INTERRUPTED, proc_tree, MOCK_MODEL);
проверка из архива `coding_path_owner.py` + шаг Windows CI; модельные профили и загрузчик (VERIFY_SOURCE_FIRST, порядок загрузки);
MVČR HW-10 до WAIT_APPROVAL; фикс MIME загрузок; фикс гигиены root-ci (маркеры SEARCH/REPLACE); реестр пропусков;
исследование R1–R8; release (OCR, Jev docs) и README из PR #72; аудиторские документы Aster6/Codex.

## В работе (агенты)
навыки (реестр Bossman) · Windows-дефекты (OS-105, publish 500, SQLite) · самоулучшение/память (агенты RAW…USER_UX) ·
перенос MEDIA-RESTART/R6 + два дефекта Studio от Aster6 (ложный PASS по длительности видео, потеря явного таймаута) ·
Owner-Run `self-improve-mvcr` готов, вливается вместе с лентой самоулучшения.

## CI
* root-ci: красный на базе d6e25fb4 (маркеры) и на f25d6fd4 (реестр) — оба исправлены; ждём прогон на голове.
* measured intelligence retention: красный на всех ветках, включая release — требует замера модели на железе владельца.
* Windows bundle: прогоны на промежуточных SHA отменялись новыми пушами; кандидат будет один, без косметических коммитов во время CI.
