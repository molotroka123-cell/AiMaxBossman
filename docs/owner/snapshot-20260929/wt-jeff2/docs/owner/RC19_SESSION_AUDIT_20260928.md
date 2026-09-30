# RC19 — аудит двух 5-часовых прогонов lead-сессии (2026-09-28)

Документ для следующих чатов: что сделано, где лежит, что открыто и с чего продолжать.
Секретов, Telegram ID, текстов диалогов и голосовых данных здесь нет.

## 1. Как продолжить

1. `git fetch origin` и сверить голову PR #84 (`claude/bossman-freeze-closure-ohvmon`). Параллельно пушат другие сессии (Claude, Codex/Aster), поэтому перед пушем всегда нужен fetch. Force-push, reset и merge в `main` запрещены.
2. Прочитать этот файл, затем `docs/owner/DECISIONS_BACKLOG.md` и `docs/owner/RC19_RELEASE_PROCEDURE.md`.
3. Ветки агентов лежат в origin как `rc19/*` (снимки, см. §5). Их ещё предстоит вливать через lead-интеграцию: `merge --no-ff`, тесты, CI на точном SHA.
4. Отчёты владельцу отправляются только в «Пульт» (companion-бот). Jeff-бот предназначен для участников, отчёты туда не шлём.

## 2. Версии и установка

| | |
|---|---|
| BASE | `762e96d2c46bdf3ede17045a46114fbd3edf2c7b` |
| FINAL (последний сертифицированный) | `84f5e0acce6a32c348330af39ff4a5874ade98a1`: **EXACT_SHA_CI=CERTIFIED 11/11** (`tools/exact_sha_certify.py`) |
| ZIP | `BOSSMAN-Windows-x64-84f5e0acce6a.zip`, 789 334 187 байт, SHA-256 `e05bf381e6db19a408ddc36f260ebdf13dbe5a5a79a6814736001397bae19019`, inputs LOCKED |
| Установка владельца | `C:\Users\asd\Bossman\app\BOSSMAN-Windows-x64-84f5e0acce6a`: **один Bossman на реальных данных** `%LOCALAPPDATA%\Bossman\CommandCenter`, backend :8801 (`backend.json` + `backend.lock`) |
| Автозапуск | задачи Планировщика при входе: `BossmanOne-1-Backend` → `-2-Jeff` (+30 с) → `-3-Companion` (+45 с); `-4-LearningSupervisor` зарегистрирована выключенной. Лаунчер `C:\Users\asd\Bossman\one-bossman\owner_one_bossman.py`. Старая `BossmanJeff` выключена, её XML сохранён |
| Бэкап перед переключением | `C:\Users\asd\Bossman\backups\owner-data-20260928-1345`: 3109 файлов, SHA-256 манифеста `17c23bab…a5c`, restore-тест 3109/3109, SQLite integrity 45/45 |
| Откат | `pwsh -File C:\Users\asd\Bossman\evidence\rc19\k\rollback.ps1` (скрипт написан, но не запускался). Базу `bcc.db` он не трогает: для полного отката нужно восстановить холодный бэкап |

## 3. Гейты и маркеры (на 84f5e0ac)

```
CORE_FREEZE=BLOCKED        (CI 11/11 CERTIFIED; блокирует только Intelligence Preservation)
WINDOWS_BUNDLE=PASS        (чистая установка, старт/стоп/рестарт, 6 откатов)
COMPUTER_USE=REAL_PASS     (a–g; h — MOCK_ONLY)
JEFF_UX=PARTIAL
YOUTUBE_K1M6A=PARTIAL
SWAPME=PARTIAL
FRESH_VIBES=PARTIAL
LEARNING=BLOCKED           (IP; локальное обучение NOT_PROVEN; 24/7 — идёт повторный прогон готовности)
BOSSMAN_1_9=READY_FOR_OWNER_TEST (тестовый RC, не релиз)
```

### Intelligence Preservation: измерено честно
- Корпус находится вне git: `C:\Users\asd\Bossman\ip-corpus-rc19\`.
  - `build_corpus.py`: seed 20260928, 940 задач, по 200 на основную метрику.
  - Источники: GSM8K, CRUXEval-O, BIG-Bench Hard (все MIT) плюс программные structured-задачи.
- Прогон: `tools/intelligence_preservation_run.py --think off`, модель Ollama `bossman-fast-qwen36-35b-a3b-q5`, 4 режима, около 70 минут.
- Гейт вернул INSUFFICIENT_EVIDENCE («baseline score is zero»: у long_context в raw-режиме 0).
- Реальные регрессии режима FULL относительно raw:
  - reasoning 0.475 → 0.44;
  - coding 0.65 → 0.58;
  - schema_argument 0.85 → 0.35;
  - task_completion 0.60 → 0.15.
- Выигрыш: structured 0.23 → 0.775.
- Причина: агент FULL (analyst, около 27 инструментов) вызывает инструменты на простых самодостаточных вопросах.
- Исправление и перемер делает агент C (ветка `rc19/m-ip`), результат ещё не получен.
- Сводку для публикации готовим только при PASS; публикует владелец.

## 4. Что исправлено в PR #84 за прогоны (lead)
- Один backend на папку данных (`bcc/backend_lock.py`): окно и CMD подключаются к нему, второй сервер завершается с кодом 5.
- Роутер при нехватке памяти уходит на бесплатную fallback-модель.
- «Открой Блокнот» больше не даёт ложный провал.
- «Пульт» держит блокировку на токен, чтобы не было двух опрашивающих бота.
- `bossman pit …` проброшен в CLI.
- Экран входа показывает путь к файлу токена, поле токена скрыто от менеджера паролей.
- Ярлык «Bossman Jeff» работает под pythonw.
- Studio записывает реальные шаги, размер и seed.
- Раннер IP получил `--think` и `--request-timeout`.
- Установщик side-by-side `tools/rc19_side_by_side.ps1`.
- Мастер-промт следующего этапа.

## 5. Ветки агентов (снимки в origin, не влиты в PR #84)

| Ветка | Агент / содержание | Состояние на момент пуша (агенты остановлены лимитом API ~14:35) |
|---|---|---|
| `rc19/k-one` | A: один Bossman + автозапуск (`tools/owner_one_bossman.*`, 16 тестов) | ГОТОВО, применено на машине |
| `rc19/m-ip` | C: исправление «лезет в инструменты» (`1ae271ea`: агент отвечает на самодостаточный вопрос напрямую) | ЧЕКПОИНТ: фикс закоммичен, **полный перемер IP не выполнен** (агент остановлен лимитом API) |
| `rc19/n-self` | D: самообучение 24/7 без Claude, отчёты в пульт по цели, уроки только после A/B ×2 и «да» владельца (`/approvals`), `start_learning_247.ps1`, `docs/owner/LEARNING_247.md` | ГОТОВО (тесты 59 + 265 зелёные). Прогон готовности: 211 циклов, 0 ошибок, $0, 0 обращений к Claude, живые тесты (пауза 2 с, STOP 1,1 с, kill/restart, кап, реальный :free) PASS; итоговый вердикт собрать командой из `evidence\rc19\n-self\CHECKPOINT.md` |
| `rc19/o-models` | E: реестр моделей (мёртвые/чужие эндпоинты, удалённые модели OpenRouter, «бесплатно» по живой цене), thinking в CMD, Studio `--llm_vision` | **WIP-коммит** (агент остановлен лимитом; тесты не подтверждены) |
| `rc19/p-green` | F: merge `i-collector`, `h-3d`, `g-rave`, `e-motion` (GREEN LIGHT владельца) + живые тесты | 4 merge сделаны, логи pytest в `evidence\rc19\p-green\`; живые тесты частично (остановлен лимитом) — проверить перед интеграцией |
| `rc19/h-3d` | G: 3D 5–10k треугольников (5 процедурных моделей), «Bossman делает модели сам» (`11ec4a6a`), нейросетевой путь | ЧЕКПОИНТ; оценка Bossman-vs-Claude в `evidence\rc19\g3d\` (`bossman_eval`, `claude_eval`) не завершена; 5-минутный прогон игры не выполнен |
| `rc19/q-parser` | H: Master Parser — CLI `bossman pit master-parse`, кнопка «Jeff · паспорта → Запустить Master Parser», `/parse` в пульте; только чтение источников | ГОТОВО в коде (21 новый тест + 627 регресс зелёные); живой сбор на копии: 399 уникальных сообщений, 8 участников, идемпотентно; **анализ локальной моделью не прогонялся** (GPU для замеров) |
| `rc19/r-jeff-next` | JN: «Jeff Next» — влит аудит-фикс Jeff, приватный blocklist участников | **WIP-коммит** (остановлен лимитом; тесты не подтверждены). Остальные пункты Jeff Next — не начаты, см. `JEFF_2_0_VISION.md` §4 |
| `rc19/s-motion` | MV: 15-с motion-видео + навык Motion Studio для локального Bossman | в работе |
| `rc19/post-rc` | Qwen-Image-2.1 помечена как некоммерческая | готово, не влито |
| `rc19/e-motion`, `rc19/g-rave`, `rc19/i-collector` | Motion Studio 1.8, Agentic Rave, сборщик данных | вливаются через `p-green` |

Ветка `rc19/integration` в origin устарела (её пушил не lead), не используйте её.

## 6. Решения владельца (хронология 28.09)
- IP: собрать публичный корпус и измерить; этот вариант выбран.
- GREEN LIGHT: Motion Studio, Agentic Rave, 3D-генерация, сборщик данных — «сразу тест». Agentic Rave работает с Claude и Codex только через подписки и официальные CLI; ключи API не используются, сессии браузера не извлекаются.
- «Всё старое закрой» означает только процессы. **Данные, собранные Jeff, не удалять.** Чистку остановили: удалены только старые папки сборок и установок RC, они заархивированы в `C:\Users\asd\Bossman\backups\rc19-cleanup\`.
- Master Parser: кнопка и команда, собирающие все разговоры для быстрого анализа паспортов.
- «Jeff Next»: полный мастер-промт владельца. Заблокированный Telegram ID хранится только в приватном файле вне git.
- Научить Bossman делать 3D-модели и motion-видео самому, локально, без Claude. Засчитывать успех «Боссман делает сам» можно только после честного замера против Claude.
- Звезду (Qwen Image 2.1, 40 шагов) остановили, GPU освобождён для замеров.

## 7. Известные дефекты и риски (открытые)
- **CMD показывает thinking** у локальных моделей Ollama: исправляет E.
- Провайдер **local-main → :8081** теперь указывает на чужой сервис (Codex app-server); агенты на model 1 падают (E).
- **Studio + Qwen-Image-2.1 зависает на CPU**: к text-to-image добавлялся `--llm_vision` (mmproj). Без него генерация на Vulkan работает: 512², 20 шагов, 13,2 с/шаг, итого 295 с. Исправляет E.
- `rc19_side_by_side.ps1 -Action StopRC` падает на `.Count` при StrictMode.
- Старая сборка 0d5d без блокировки backend может быть запущена другой сессией вручную. Порт :8801 занят, но старый Jeff запустить можно.
- Старое окно «Bossman CMD» (сборка e97bad5b) у владельца открыто.
- «Пульт» из новой сборки не проверен (NOT_VERIFIED): токен держит мост lead-сессии `tg_session_bridge.py` (пересылает сообщения владельца в сессию). По завершении сессии нужно остановить мост и выполнить `Start-ScheduledTask BossmanOne-3-Companion`.
- Выход и вход в Windows для проверки автозапуска не выполнялись (NOT_VERIFIED): это действие владельца.
- Игра BossBlocks (отдельный локальный репозиторий `C:\Users\asd\Bossman\games\bossblocks`) и её 3D-модели написаны агентами Claude. Локально всё работает, но «Боссман делает сам» пока не доказано.

## 8. Доказательства (локально, вне git)
`C:\Users\asd\Bossman\evidence\rc19\`:
- `lead\`: FINAL_REPORT_RC19.md, логи gate2, exact-sha-certify-*.json, диагностика qi21-*;
- `k\`: переключение на один Bossman, бэкап, откат;
- `b\`: Computer Use;
- `c\`: Jeff;
- `d\`: learning, K1m6a, journeys;
- `j\`, `j2\`, `g3d\`: игра и 3D;
- `cleanup\`: инвентаризация и actions.json;
- `p-green\`, `mv\`: живые тесты и видео.

## 9. Следующий шаг (один)
Дождаться результатов агентов C и D (перемер IP, готовность 24/7). Затем lead вливает ветки `rc19/*` в один кандидат, получает CI на точном SHA, собирает новый ZIP и переключает `owner_one_bossman.ps1 -Action Switch`. После этого идёт прогон кликами по трём ярлыкам и итоговый отчёт в «Пульт».
