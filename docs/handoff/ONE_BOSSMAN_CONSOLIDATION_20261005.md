# Один Bossman — сведение веток (чекпоинт 05.10.2026)

Рабочая линия: **`goal/bossman-self-improvement-tree-20261005`** (HEAD на момент записи — см. `git log -1`;
последний установленный у владельца пакет — `b96e2a7c`, проверка пакета PASS, откат
`Bossman\bugtest-20261001\tree-1005\switch-b96e2a7c\rollback.ps1`).

`origin/main` полностью влит в линию (21 docs-коммит, конфликт только README — оставлен новый README + ссылка 24.09).
`origin/release/bossman-owner` — новых коммитов относительно линии нет.

## Что уже в линии
| Источник | Как попало | Что дало |
|---|---|---|
| `claude/bossman-1.9-owner-bugtest-20260930` @ 1a82797f | merge `6af9d0ca` | Jeff 2.0 (`pit/j2/` 10 модулей), звонки, паспорт, master_parser 2.0, model_policy, CosyVoice-слот |
| `cv/j` 4cb14095, a97c30dd, 4c5cf52b, 49e9f943, 3bfbf0af | cherry-pick (другие SHA!) | голос только по просьбе, замена ответа «я — модель X», red-team история, Liquid/LFM, текст первым |
| `origin/main` | merge `bb6a6255` | owner-run docs, WebDesigner OSS ledger, Jev Twitch spec |
| свои коммиты 05.10 | — | дерево с «заработанными» листьями, cloud-workers (Nemotron Ultra free), genjutsu film + skill, фиксы безопасности |

Тесты на линии (локально, Windows): Jeff-набор 1781 passed; дерево+исполнители 26; пульт-контракты 272; auth 121 (core) + 43 (CC);
evolution 23; терминал-безопасность 51. Полный CI по SHA — **не подтверждён** (проверить первым делом).

## Предложение по каждой ветке с невлитыми коммитами
| Ветка | Что там | Предложение |
|---|---|---|
| `origin/pr89-latest` (5 docs 2.1) | архитектура autonomous operator, 5 кандидатов возможностей, Colibri отложен | **влить** (docs-only), затем закрыть PR #89 как поглощённый |
| `origin/claude/bossman-freeze-closure-ohvmon` (1 docs) | ссылка на аудит 02.10 | **влить** вместе с pr89-latest (тот же коммит по смыслу) |
| `origin/handoff/continuation-20260929` (3 docs) | снимок docs 29.09, continuation prompt | **влить в `docs/owner/archive/`** или оставить архивом — не код |
| `origin/cv/j` | 4 фикса | **уже в линии** (cherry-pick, проверено тестами) → ветку можно архивировать |
| `origin/claude/telegram-live-calls-s7-work` (7) | те же Jeff-фиксы + calls docs/acceptance + `a0087401` DACL Windows | **проверить** `a0087401` (DACL не обнуляется у файлов) и `a6a1b03b`/`12db1299` против линии: если отсутствуют — cherry-pick с тестами; остальное дубли |
| `origin/wip/cv-d|cv-e|autonomy-b|autonomy-c|motion56-20260929` | stash-снимки (`On …: wip`) | **не вливать как есть**: это сохранённые незакоммиченные правки. Просмотреть diff каждого; полезное — отдельными коммитами с тестами |
| `origin/feat/bossman-autonomy-funding` (1 docs) | пакет заявки на финансирование (NOT_SUBMITTED) | **влить в docs** или оставить — решение владельца |
| `origin/scratch/root-ci-debug-19` | отладка CI | **удалить** (scratch) — только с согласия владельца |

Правило: ничего не force-push, `main`/`release` не трогать напрямую; итог — один PR линии → `main`, merge нажимает владелец.

## Открыто (честно)
- **Самоулучшение не доказано**: 3 живые попытки в зоне `pit/discovery.py` (GLM 5.3 Flash ×2, Nemotron Ultra) — без принятого
  изменения; лист не получил «проверено». Найденный по пути баг (одиночный суррогат → HTTP 400) исправлен.
- **Jeff web**: SearXNG не отвечает (doctor `web FAIL`).
- **Звонки**: `telegram-calls/credentials.enc` перезаписан повышенным процессом 05.10 14:34 и не расшифровывается — `bossman call setup` владельцем.
- **Пульт → компьютер**: агенту пульта (локальная Qwen3.6-35B) выданы `computer.observe/act`; живой прогон владельца ещё не выполнен
  (первая попытка: `/confirm` без кода был отклонён → исправлено `98e6b073`).
- **Аудит безопасности 05.10**: исправлены #4 терминал-режим, #6 WS после отзыва, #7 logout с чужого порта, #8 панель после выхода,
  #1 WS-фильтр по скоупу (core), #2 платные воркеры в бюджете цикла. **Открыты**: #3 индекс кода видит чужой scratch через
  предка-корень (low), #5 привязка sha256 удалённого скрипта теряется в диспетчере (medium), #9 секрет OpenRouter в CI без
  protected environment (medium; нужна настройка environment в GitHub владельцем).
- **Голос владельца**: Chatterbox уже забракован владельцем; кандидаты Qwen3-TTS-0.6B (Apache-2.0), CosyVoice 3; ASR GigaAM-v3. Нужна запись 1–3 мин.
