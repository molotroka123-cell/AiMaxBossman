# Bossman 1.9 → 2.0: handoff вечера 2026-09-30 (ПК владельца)

**Статус: `PARTIAL`.** Сделан и запушен большой объём по девяти направлениям (ниже); заморозка/FREEZE PASS **не заявляется**,
самообучение в режиме 24/7 **не доказано** (доказана ограниченная песочничная репетиция), реальный звонок Telegram и ролик
Higgsfield ждут владельца. Всё, что не запускалось на машине владельца, помечено `NOT_RUN`.

## Где

- Репозиторий `molotroka123-cell/AiMaxBossman`, рабочая ветка `claude/bossman-1.9-owner-bugtest-20260930` (draft PR #89, база `feat/bossman-autonomy`).
  Локально: `C:\Users\asd\Bossman\wt-bugtest-0930` (ветка `owner/bugtest-20260930`). Вершина: `git rev-parse origin/claude/bossman-1.9-owner-bugtest-20260930` (последний кодовый коммит — перф `361a7a7c`, дальше только документы). Итоговый аудит закрытия 2.0 и план на завтра: `docs/owner/AUDIT_2_0_CLOSURE_20260930.md`.
- В ветку влито за день: линия Telegram-звонков (PR #87), мастер-промпт владельца, все полосы роя (ниже). `main`, `release/*`, теги — не тронуты, force-push не было.
- Рой: `C:\Users\asd\Bossman\swarm-20260930\` (COMMON.md, `reports\*.md` по полосам, `evidence\` скриншоты, `tools\` сторожа Jeff). Не в репозитории.

## Что сделано (по полосам; каждая исправленная ошибка имеет тест, красный на старом коде)

| Полоса | Итог | Вердикт |
|---|---|---|
| Новый чат `/chat.html` | прогон 20 пунктов §6 в Chromium и Edge: 19 PASS, 1 PARTIAL (пункт 9: карточка Rave проверена на подмене); найдены и исправлены B1–B10 (picker 404, `<think>`, autolink, F5 теряет LOCAL, ложный «Связь потеряна» и др.) — `docs/owner/runs/RUN_20261001_CHAT.md` | PASS (repo-local) + браузерный прогон агента; не приёмка владельца |
| Jeff 2.0 (ядро) | 3 бага окна (провайдер после возврата модели, контекст прошлой реплики, история после F5), heartbeat, кризис-детектор RU/UK/EN, защита от травли названных людей, настроение не раскрывается, identity-фильтр, blocklist исполняется — `docs/owner/runs/JEFF_CORE_20260930.md` | PASS (repo-local); D5/D9 PARTIAL, D12 NOT_DONE |
| Независимый аудит Jeff | 13 дефектов (2 HIGH), таблица проб и рекомендации — отчёт роя `reports\jeff-audit.md` | закрыты jeff-core |
| Живой Jeff | переведён на новый код (снимок `64af9e00`, `pit watch`), overlay «грубиян» включён; бэкап `backups\pit-before-jeff2-20260930-1105`; откат/выключение настроения — `C:\Users\asd\Bossman\jeff-live-0930\rollback-and-mood.ps1` | запущен, поведение на участниках `NOT_RUN` |
| Telegram-звонки | готово до входа владельца: ACL-баг Windows (пустой DACL) исправлен (в т.ч. `bcc/auth.py`), один префикс `/api/telegram/calls`, звонок не пишет текст на диск (j2 deny-by-default), 2 независимых аудита (32 находки), doctor-строки, плоскость `calls` в «Остановить всё»; 26 проверок панели в Chromium — `docs/telegram-calls/ACCEPTANCE.md` | реальный вход/звонок `OWNER_REQUIRED` |
| Автономия | ограниченная петля (STOP на всех уровнях, защита своих правил, бюджет, оценка до одобрения, опыт как UNVERIFIED-урок, супервизор с лимитами); репетиция в песочнице: Claude(writer)+Claude/Codex(review) по одному sha, staging, стоп на USER_APPROVAL, STOP убил живой `claude.exe` за 0.4 с — `docs/autonomy/BOUNDED_SELF_IMPROVEMENT.md`, артефакты `docs/owner/runs/autonomy-rehearsal-20260930/` | `SANDBOX_REHEARSAL` (пин тестовый, не владельца); цикл на корне владельца `OWNER_REQUIRED` |
| Ops / Browser P0 | клик «Оплатить/Отправить/Удалить…» теперь через approval, password агентом запрещён, жизненный цикл сессий, Job Object (нет осиротевших `sh.exe`); корень `0x80000004` `NOT_FOUND` — `reports\ops.md` | PASS (repo-local); дыры: `type=button`+fetch, GET через `browser.open` |
| Веб-доступ | листинг/чтение/браузер/SSRF проверены, 11 дефектов исправлены — `docs/owner/runs/WEB_ACCESS_20260930.md`; у агентов владельца веб-чтение выключено (нужны `BOSSMAN_WEB_RESEARCH_ENABLED`, `BOSSMAN_OSIRIS_ENABLED`), общий поиск требует свой SearXNG | PARTIAL / `OWNER_REQUIRED` |
| Agentic Rave | проверка версии Claude CLI, честный STOP, Apply, пул собственных аккаунтов (выкл по умолчанию), каталог «нет в каталоге», market, K1m6a — `docs/v1.9/AGENTIC_RAVE.md` | PASS (repo-local); живой rave `NOT_RUN` |
| Отчёты в пульт | модуль `python -m bcc.telegram_companion.owner_report --file X [--send]` (dry-run по умолчанию) | PASS (repo-local), 4 реальных отправки |
| Music Studio | причина «ConnectError»: ACE-Step не стоял; честный health (6 статусов), кнопки запуска/остановки; ACE-Step установлен, треки 20 и 30 с сгенерированы через Bossman — `docs/music/MUSIC_STUDIO.md` | PASS (repo-local) + REAL_LOCAL; 60–90 с/варианты NOT_RUN, LM 4B BLOCKED |
| UX-обход всех страниц | 49 маршрутов, 294 из 461 элементов, 14/14 найденных дефектов закрыты — `docs/owner/runs/UX_SWEEP_20260930.md` | PASS (repo-local + браузер); инцидент с профилем описан в аудите |
| Скорость Bossman | `/api/tasks` ×6.4 (101→2 SQL), `/health` ready −44…−51%, `/api/apps` p95 1226→13.5 мс — `docs/audits/PERF_20260930.md` | IMPROVED (измерено, синтетическая БД); UI-загрузка не мерилась |

Бесплатные модели: Nemotron-3-Ultra 550B работает через NVIDIA NIM (агент 25 на :8801, $0). Ключ OpenRouter («test key 2026-09-24») **просрочен**.

## Не подтверждено / не сделано

- Полный зелёный CI на финальном SHA ветки: ждёт раннеров; полный локальный прогон — см. `docs/owner/AUDIT_2_0_CLOSURE_20260930.md` §2: command-center 8183 passed / 3 failed (разобраны), корневые 2877 passed / 6 env-failed, node 119/119.
- Ролик Genjutsu (Higgsfield): видео 15 с 9:16 и фото загружены, предрасчёт 720p = 105 кредитов (480p = 45), на аккаунте 0 кредитов — `OWNER_REQUIRED`; рассылка через Jeff после результата.
- Не сделаны полосы: providers (`model_billing`, prompt caching, телеметрия tok/s), motion (перенос `wip/motion56`), jeff-j2 baseline-harness («вдвое сильнее» — только измерением, числа нет), real-цикл JEFF-0042.
- Живые проверки: микрофон, Claude/Codex rave, `terminal.run` в чистом data-dir, рассылка участникам, голосовой ответ Piper новой сборкой.

## Что нужно от владельца (по одному действию)

1. `bossman autonomy constitution pin` в обычном интерактивном терминале (пин не обойти агентом — по замыслу). Затем `bossman autonomy plan`, `bossman autonomy run --max-cycles 1 --max-cli-turns 8`.
2. Higgsfield: кредиты (Plus $49/мес ≈ 1000 кредитов) или API-ключ; новый ключ OpenRouter.
3. Вход Telegram для звонков: экран «Telegram-звонки» (api_id/api_hash с my.telegram.org, номер, код, 2FA — вводит только владелец); собеседник выбирается в UX.
4. К3: переключить автозапуск/ярлыки на 1.9 (`owner_one_bossman.ps1`). Сейчас автозапуск после перезагрузки поднимает СТАРЫЕ backend/Jeff; новый живой Jeff запущен вручную.
5. Веб: флаги `BOSSMAN_WEB_RESEARCH_ENABLED=1`, `BOSSMAN_OSIRIS_ENABLED=1` и перезапуск; решение по SearXNG.
6. Ollama: `OLLAMA_KEEP_ALIVE=-1` (иначе холодная загрузка модели Jeff 34–66 с > дедлайна 60 с; сейчас держит страж `swarm-20260930\tools\jeff_keepwarm.py`).

## Команды следующего прогона

```powershell
cd C:\Users\asd\Bossman\wt-bugtest-0930
git fetch origin '+refs/heads/*:refs/remotes/origin/*'; git log --oneline -5 origin/claude/bossman-1.9-owner-bugtest-20260930
$env:PYTHONPATH="$PWD\command-center;$PWD\bossman-core;$PWD"; $env:PYTHONUTF8='1'
# тестовый экземпляр (НЕ данные владельца): BCC_DATA_DIR=C:\Users\asd\Bossman\bugtest-20261001\data, порт 8832, run-bossman.cmd
# Jeff: C:\Users\asd\Bossman\jeff-live-0930\rollback-and-mood.ps1 -Action MoodOff|MoodOn|RollbackOld
```

## Rollback

Ничего не релизилось. Установленный backend 84f5e0ac и данные `%LOCALAPPDATA%\Bossman\CommandCenter` не менялись, кроме: (а) Jeff переведён на новый код и получил `pit-v1.7\jeff-settings.json` (бэкап выше; откат `-Action RollbackOld`), (б) создан агент 25 (Nemotron NIM) на :8801. Последний целиком зелёный кандидат для сравнения — `0139fe27`.

## North Star и Terminal Run

- Уровень: `SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT` — не повышен: реальный цикл на корне владельца не запускался, репетиция в песочнице на тестовом пине ступень не закрывает.
- Terminal Run = та же поверхность того же Bossman: чат, `bossman chat/exec/autonomy/call`, дашборд и Telegram используют один backend, задачи, память, approvals и STOP.
- Маркеры: `CHAT_UX=PASS(19/20, repo-local+browser)`, `JEFF_UX=IMPROVED (audit D1–D13 closed except D5/D9/D12)`, `LEARNING=NOT_PROVEN (WEIGHTS_UNCHANGED)`, `COMPUTER_USE=REAL_PASS a–g (26.09, не на этом SHA)`, `YOUTUBE_K1M6A=PARTIAL`, `SWAPME=PARTIAL`, `FRESH_VIBES=PARTIAL`, `TELEGRAM_CALLS=READY_UNTIL_OWNER_LOGIN`, `BOSSMAN_1_9=READY_FOR_OWNER_BUG_TEST (чат, Jeff, звонки до входа); FREEZE не заявляется`.
