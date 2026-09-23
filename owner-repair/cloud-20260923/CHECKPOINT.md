# CLOUD_PREPARE — чекпоинт облачного этапа 2026-09-23

Ветка: `claude/bossman-cloud-closure-owner-a6s1ki` → PR #74 → `integrate/owner-final-20260922` (PR #73) → `release/bossman-owner`.
Статус этапа: **IN_PROGRESS**. Компьютер владельца не запускался. OWNER_HARDWARE_CERTIFIED не заявляется.

## Решение владельца 2026-09-23: эта линия — рабочая 1.0
Новые коммиты в `release/bossman-owner` — работа Aster над **1.1**; она переезжает в отдельную ветку.
В линию 1.0 они больше **не вливаются**. Уже влитое из release — только документы (OCR-подборка,
TOMORROW_* runbook, `docs/JEV_*.md`), кода 1.1 в линии нет. Не влит: `df30cf26` (weekly P0 upstream candidates, 1.1).
Jev готовится по отдельной команде владельца, но только выключенным по умолчанию (shadow) — поведение 1.0 не меняет.
Целевая ветка для слияния 1.0 после переезда 1.1 — подтверждает владелец (PR #74 → integrate/owner-final → PR #73).

## Исправлено в линии 1.0 (воспроизведение → регрессия красная до фикса → зелёная)
| Дефект | Коммит |
|---|---|
| Нет исполнителя coding path для локальных моделей; готовность по строке команды | ca53461e |
| venv терялся (симлинк интерпретатора) · кавычки в пути с пробелами на Windows · сироты sidecar по таймауту | ca53461e, 33f3fb9b |
| root-ci: маркеры SEARCH/REPLACE; устаревший реестр пропусков | 90e30268, f979227e |
| HTML-ошибка, сохранённая как .pdf, считалась PDF (HW-10) | 75fa46b3 |
| OS-105: приватность каталогов аккаунтов на Windows через ACL | 36d1da77 |
| /api/testing/publish: 500 без git / при зависании / на кириллице | ddf74253 |
| bcc.db держится процессом-потомком после stop() (WinError 32) | f868de1d |
| capability-пробы помечали reasoning-модель неспособной | 925263da |
| ffmpeg/git в инструментах агента без таймаута / сироты | b2130032 |
| Command Center CI: правка профиля Telegram без bossman-core → 500; фикстуры Fleet проверяли не тот импорт | 51f0b68a |
| Studio: видео не той длины считалось PASS; явный hard_timeout_s терялся (находки Aster6) | eee89e6f |
| MEDIA-RESTART слой 1: sd.cpp рождается приостановленным и возобновляется только внутри Job Object | 44c6bff3 |
| R6: действие computer-use, вставшее в очередь за замком, выполнялось после STOP | 908ccd15 |
| Owner STOP для coding-задачи не убивал дерево сайдкара | 45e0f671 |
| Owner-Run self-improve-mvcr говорил не на контрактах своих соседей (статус манифеста вместо файлов, нет --data-dir/--work-dir/transfer) | bb2ef028 |

## Открыто (точный список)
* Без таймаута остаются (чинится агентом, воспроизведение → регрессия → фикс): video_factory/ffmpeg.py:114/142/161, projects/runner.py:194, benchmark/engine.py:39/598/632,
  sandbox/netguard.py:43, bcc/desktop_install.py:290, telegram_companion/claude_bridge.py:35.
* /healthz: readiness не зеленеет после рестарта, если последний тест модели старше 30 мин (контракт MF-032) — решение владельца.
* scn_23 (OS-105) проверяет S_IMODE==0o700 — бессмысленно на Windows (harness).
* measured intelligence retention — нужен замер модели на железе владельца.
* Windows ZIP на финальном SHA ещё не собран/не проверен.

## Локальные прогоны (Linux-контейнер, не Windows)
* command-center полный, с bossman-core: 4119 passed, 15 failed, 7 errors. Все 22 — окружение прогона
  (сирота File Commander на порту 8911 от параллельного прогона; дерево менялось во время прогона):
  на чистом дереве медиа 21/21, apps_files + smoke 5/5, b..e + медиа 383 passed.
* bossman-core полный (лента Windows-дефектов): 3298 passed, 52 skipped.

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

## Слито позже (ленты агентов)
навыки (11 шт., реестр Bossman, UNVERIFIED, прав не дают) · Windows-дефекты · самоулучшение/память (агенты RAW…USER_UX,
рецепты через LearningStore) · MEDIA-RESTART/R6 + Studio · Owner-Run `self-improve-mvcr` (стадия skills) ·
CI: job `core-runtime` (ubuntu+windows, coding path с bossman-core) и шаг Windows «Owner-Run plan из архива» (d383e574).

## В работе (агенты)
subprocess без таймаута (список выше) · Jev browser fast-path — только shadow, выключен по умолчанию.

## CI
* root-ci: красный на базе d6e25fb4 (маркеры) и на f25d6fd4 (реестр) — оба исправлены; ждём прогон на голове.
* measured intelligence retention: красный на всех ветках, включая release — требует замера модели на железе владельца.
* Windows bundle: прогоны на промежуточных SHA отменялись новыми пушами; кандидат будет один, без косметических коммитов во время CI.
