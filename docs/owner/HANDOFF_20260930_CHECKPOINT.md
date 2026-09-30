# Bossman 1.9 — handoff чекпоинта 2026-09-30 (облачная сессия)

**Статус: `BLOCKED`** для полного объёма ночи: Jeff 2.0 (три известных бага окна Jeff),
ограниченный цикл самоулучшения и доводка CMD/Browser/Computer Use/Rave/Motion/YouTube/SwapMe/FreshVibes
в этом чекпоинте не сделаны (владелец остановил ночь на чекпоинте). **Новый desktop-чат готов к
ручному баг-тесту владельцем** после зелёного CI на финальном SHA — это `READY_FOR_OWNER_BUG_TEST`
только для чата, не для всего 1.9. В облаке не запускались тесты, бенчмарки, owner-сценарии и
аппаратные прогоны (правило владельца): всё ниже — `PASS (repo-local)` не заявляется, статус
реализованного — `NOT_RUN`, пока его не подтвердит CI и ручной прогон.

## Где

- Репозиторий: `molotroka123-cell/AiMaxBossman`
- Ветка: `claude/bossman-1.9-owner-bugtest-20260930` (draft PR #89, база PR `feat/bossman-autonomy`)
- Base SHA: `4c770578` (`origin/feat/bossman-autonomy` — самый полный тип 1.9: jeff-2.0, autonomy-a/b,
  rc19/t-jeff-admin, a19/streaming, rc19/integration уже внутри)
- Финальный SHA чекпоинта: `<FINAL_SHA>`
- Canonical `release/bossman-owner` и `main` не трогались; force-push не было; тегов/релизов нет.

## Что влито (merge-коммиты, без потери поведения)

| ветка | что даёт |
|---|---|
| `fix/k1m6a-discovery-limit` | freeze-кандидат 5 `0139fe27` (UI-sweep фиксы) + K1m6a `--limit` |
| `feat/bossman-autonomy-c` | обязательный identity/disclosure-фильтр Jeff, model policy (Liquid/LFM), memory-poisoning gate, pre-TTS audit |
| `feat/bossman-perf-2.0` | probe cache для пяти медленных health-эндпоинтов |
| `cv/e` | Ollama `/v1` с `reasoning_effort=none` (конфликт решён: работает и в streaming-пути) |
| `claude/bossman-freeze-closure-ohvmon` | owner-run debug recorder (`tools/bossman_debug_recorder`); Motion-конфликты решены в пользу линии 1.9 (Lottie уже там) |
| `handoff/continuation-20260929` | continuation-промпт 2026-09-29 и план JEFF-0042 |

Не влиты намеренно: Telegram live calls (вне scope), funding-доки. WIP-снимки: `wip/autonomy-b/-c`
уже в HEAD; `wip/cv-d` (перезапуск не теряет Studio-генерацию), `wip/cv-e` (снятая модель → `unavailable`),
`wip/motion56` — **не перенесены**, инструкции переноса в `docs/owner/night-20260930/maps/apps-b.md` §4.

## Что реализовано в этом чекпоинте

1. **Новый desktop-чат** (`/chat.html`, `bcc-desktop --chat`, страница «Чат» в классической оболочке) —
   описание, эндпоинты, горячие клавиши и чек-лист ручного теста: `docs/v1.9/DESKTOP_CHAT_UX.md`.
   - Бэкенд `bcc/features/chat_threads.py`: треды = сессии `bossman chat` (один Bossman: тред из CMD
     виден в окне и наоборот), отправка через настоящие `/api/tasks/preflight → /api/tasks → /run`
     (тот же допуск, идемпотентность, approvals, STOP), вложения как DATA (не инструкции), options без
     падений на чистой установке, `Last-Event-ID` для SSE; CLI больше не теряет ходы, дописанные окном.
   - UI: история/поиск/проекты слева, чат по центру, composer (модель, вложения, микрофон → локальный
     whisper, отправка, STOP), настоящий streaming с отменой/ошибкой/переподключением, панель
     «Thinking & Actions» (кратко/план/действия/проверка/источники — без скрытых рассуждений модели),
     Agentic Rave, память, инструменты, Sphere, reduced-motion.
   - Open-source: из 30+ кандидатов **ни один не прошёл 10/10** (лучшие highlight.js и remend — 8/10:
     не лучше встроенного и не закрывают риск; DOMPurify не нужен — в чате нет HTML-стоков). Вместо
     зависимостей сделаны 8 измеренных паттернов: стриминг только «хвоста» (62 KB ответ: 8.4 → 0.24 мс
     на дельту), без живых полуссылок, `aria-busy` + одно объявление, прилипание к низу по намерению +
     «К последнему сообщению», CSP + Trusted Types, `content-visibility`, баннер переподключения с
     «Сейчас», «Скопировано ✓» на кнопке. Доказательства: `docs/v1.9/DESKTOP_CHAT_UX.md`.
2. **CI-исправления на этой ветке** (каждое — с причиной в коммите):
   - secret scan: фейковый ключ в тесте Jeff 2.0 помечен `ci-secret-scan: allow` (`b6018ee3`);
   - SAST (bandit B613): литеральные bidi-символы в regex Jeff 2.0 → `\u`-экранирование, паттерн идентичен (`242e2415`);
   - 4 теста autonomy: Windows-пути на POSIX теперь «вне worktree»; staging-пробы fail-closed, если
     код не из кандидата (была реальная fail-open дыра); тест identity учитывает обязательный фильтр (`fa0bb9c8`);
   - py3.11: `model_guard` больше не зависает на `stop()`, если 3.11 проглотил отмену (`08e54a5b`).
3. **Материалы продолжения**: `docs/owner/night-20260930/` — 8 карт подсистем с file:line,
   контракт API чата и точные спецификации 10 полос (`LANE_SPECS.md`).

## Не подтверждено (NOT_RUN / INSUFFICIENT_EVIDENCE)

- Весь новый код: тесты написаны, **не запускались** в облаке; браузерное поведение чата не проверялось.
- CI на `<FINAL_SHA>`: ждёт раннеров (очередь в эту ночь ~6 ч).
- `windows paths (py3.12)` → `test_terminal_runs_in_project_host` (exit `0x80000004`): упал на первом
  прогоне `8cfa0481`, прошёл на втором прогоне того же SHA — нестабилен, корень не найден.
- Intelligence Preservation: красный по замыслу — нужен владельческий замер на железе.
- CSP/Trusted Types и `content-visibility` в Chrome/Edge владельца — проверить в консоли (0 CSP-нарушений).

## Что НЕ сделано (следующий чекпоинт, спецификации готовы в `LANE_SPECS.md`)

| полоса | суть |
|---|---|
| jeff-core | 3 бага окна Jeff (корни найдены: `maps/jeff-jev.md` §4): восстановление после модели, контекст прошлого хода, история после F5; heartbeat окна; security health/speak; перенос `wip/cv-d` |
| jeff-j2 | j2-wiring (`served_by`), harness сравнения с baseline (цель «вдвое сильнее» — только измерением) |
| autonomy | kill switch в `bcc.autonomy`, защита собственных правил, бюджет, оценка до одобрения, опыт → LessonBook (WEIGHTS_UNCHANGED); автономное применение остаётся OFF |
| ops | **P0 Browser**: клик «Оплатить/Опубликовать/Отправить» сейчас AUTO — нужен gate; терминал/браузер после рестарта; Computer Use approvals |
| rave-apps | Claude CLI ≥ 2.1.259 для `--permission-prompts none`, Apply в UI, `wip/cv-e`, market data-dir, K1m6a batch |
| motion | перенос `wip/motion56`, восстановление сирот, библиотека в продукте |
| providers | `model_billing` (сейчас чат показывает locality по `models.kind`), prompt caching Anthropic, телеметрия tok/s/TTFT/кэш в `run.usage` |
| owner-report | отправка отчёта владельцу через настроенный канал Пульта (`python -m bcc.telegram_companion.owner_report`) |

Отчёт в Telegram: **NOT_SENT** — в облачном контейнере нет настроенного канала Пульта; отдельной
CLI-отправки в репо пока нет (полоса owner-report).

## Завтрашний прогон (CMD/UX владельца)

1. CI на финальном SHA (PR #89) или сертификация:
   `python tools\exact_sha_certify.py --sha <FINAL_SHA> --fetch --repo molotroka123-cell/AiMaxBossman --output exact-sha-certification.json`
   (ветка не объявлена в `tools/release_candidate.json` — ожидаемо `NOT_CERTIFIED`, это не релиз).
2. Recorder до старта: `tools\bossman_debug_recorder\START-RECORDER.cmd`.
3. Вариант A — side-by-side сборка, данные владельца не трогаются:
   ```powershell
   pwsh -File tools\rc19_side_by_side.ps1 -Action Build -Sha <FINAL_SHA>
   pwsh -File tools\rc19_side_by_side.ps1 -Action Install -Sha <FINAL_SHA>
   pwsh -File tools\rc19_side_by_side.ps1 -Action Start -Sha <FINAL_SHA> -Port 8831 -DataDir "$env:USERPROFILE\Bossman\rc19-data\bugtest-20261001"
   ```
   Вариант B — из исходников в отдельном worktree:
   ```powershell
   git fetch origin claude/bossman-1.9-owner-bugtest-20260930
   git worktree add ..\wt-bugtest-20261001 <FINAL_SHA>
   cd ..\wt-bugtest-20261001
   $env:BCC_DATA_DIR = "$env:USERPROFILE\Bossman\bugtest-20261001"
   .\start-bossman.ps1 -Port 8832
   ```
4. Чат: во второй консоли с тем же `$env:BCC_DATA_DIR` — `bcc-desktop --chat --port 8832`
   (или `http://127.0.0.1:8832/chat.html` в браузере; для варианта A порт 8831), чек-лист —
   `docs/v1.9/DESKTOP_CHAT_UX.md` §6. CMD: `bossman chat` → тот же тред в окне; `bossman stop --all`.
5. После: `tools\bossman_debug_recorder\STOP-RECORDER.cmd`; баги — с номером шага чек-листа.

## Rollback

- Ничего не релизилось; установленная сборка и `%LOCALAPPDATA%\Bossman\CommandCenter` не тронуты.
- Вариант A: `pwsh -File tools\rc19_side_by_side.ps1 -Action StopRC -Sha <FINAL_SHA>`, затем
  `-Action RollbackCheck -Sha <FINAL_SHA>` (прежние установки целы) и удалить папку RC-установки.
- Вариант B: `git worktree remove ..\wt-bugtest-20261001`; данные в отдельном `BCC_DATA_DIR`.
- Последний целиком зелёный кандидат для сравнения — `0139fe27` (freeze-кандидат 5, 11/11 обязательных workflow).

## North Star и Terminal Run

- Уровень: `SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT` (реальный цикл не запускался; JEFF-0042 — только с разрешения владельца).
- Terminal Run = тот же продукт: чат использует тот же backend, задачи, память, approvals и STOP;
  треды чата и `bossman chat` — одно хранилище.
- Маркеры: `CHAT_UX=READY_FOR_OWNER_TEST (NOT_RUN)`, `JEFF_UX=NOT_READY`, `LEARNING=NOT_PROVEN (WEIGHTS_UNCHANGED)`,
  `COMPUTER_USE=REAL_PASS a–g (26.09, не на этом SHA)`, `YOUTUBE_K1M6A=PARTIAL`, `SWAPME=PARTIAL`,
  `FRESH_VIBES=PARTIAL`, `BOSSMAN_1_9=BLOCKED`.
