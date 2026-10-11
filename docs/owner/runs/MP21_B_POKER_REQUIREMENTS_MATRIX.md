# MP21 Track B — Poker: матрица требований (заморожена 11.10.2026)

Объект аудита: приложение владельца **Poker Train** (`C:\Users\asd\Bossman\apps-local\pokertrain`, GitHub
`molotroka123-cell/pokertrain`, master `6bc72bf`, v2.2.0 «IceCrown Poker Club») и модули Bossman
`apps/poker-botlab`, `apps/poker-vision`, `apps/poker-lora` (ветка poker `ff024ef0`, уже предок линии
`feat/bossman-genjutsu-jeff-unified-20261010`).

Коммиты работы (локальные ветки `mp/b-poker-20261011`, **не отправлены**, force не использовался):

| репозиторий | SHA | что |
|---|---|---|
| pokertrain (worktree `C:\Users\asd\Bossman\wt-mp-b-pokertrain-20261011`) | `cd78920` | сайд-поты, рейк, мин-рейз, пропуск обязательного колла, seed RNG, CI эквити, позиции HH + тесты движка |
| pokertrain | `4b6f8f3` | единое определение VPIP (12 мест) + тесты |
| pokertrain | `79ab05a` | `tools/simulate-session.mjs` — сидированная сессия в формате экспорта |
| pokertrain | `2f3171f` | `docs/RUN_LOCAL_RU.md`, CLAUDE.md |
| Bossman integration (worktree `C:\Users\asd\Bossman\wt-mp-b-poker-20261011`, база `ff024ef0`) | `1e3d25d0` | рейк в движке/арене, `mc_equity_stats`, `botlab report` + тесты |
| Bossman integration | `6d9f2020` | README_RU: рейк и отчёт |

Статусы: **ТВ** = технически выполнен (есть прогон, путь к доказательству); **Код** = есть в исходниках, в этом
аудите не запускалось; **ЧВ** = частично; **НЕТ**; **NOT PROVEN**.

## 1. Игровые сценарии

| # | Требование | Доказательство | Статус |
|---|---|---|---|
| 1.1 | Турнирные форматы WSOP Main/Daily, EPT, WPT, HARDCORE 6-max | `src/data/tournamentFormats.js`, `src/tournament/TournamentDirector.js` | Код (UI-прогон не делался) |
| 1.2 | Кэш NL2–NL25 (+GG-варианты), авторебай, 6-max | `src/data/cashFormats.js`, `App.jsx` `isCash` | ТВ на уровне движка: `MP21_B_POKER_evidence/session_NL10_seed11.json` (300 раздач) |
| 1.3 | Жизненный цикл раздачи, блайнды, анте/BB-анте, улицы | `src/engine/GameEngine.js` | ТВ: `test/settlement.test.js`, `test/engine_rules.test.js` |
| 1.4 | Сайд-поты и сплит-поты | `src/engine/potSettlement.js` (новый) | **ИСПРАВЛЕНО** (было неверно, аудит §2), ТВ: 4 теста |
| 1.5 | Мин-рейз = размер предыдущего рейза | `GameEngine._applyAction` | **ИСПРАВЛЕНО** (было «= BB»), ТВ: тест |
| 1.6 | Игрок, стоящий перед all-in, обязан действовать | `GameEngine._runStreet` | **ИСПРАВЛЕНО** (был пропуск хода), ТВ: тест |
| 1.7 | Короткий all-in не переоткрывает торги | `GameEngine._runStreet` | НЕТ (известное отклонение, не исправлялось) |
| 1.8 | Адаптивные ИИ (TAG/LAG/Nit/Maniac…), Claude-бот HARDCORE | `src/engine/ai.js`, `adaptiveAI.js`, `claudeAI.js` | Код; Claude-бот ходит в облачный API — не запускался |
| 1.9 | Дриллы (17 модулей: RFI, 3-bet, push/fold, pot odds, river…) | `src/drills/*` | Код (UI не прогонялся) |
| 1.10 | Тест-матч «бот Bossman vs Poker Train владельца» | `apps/poker-vision/evidence/trainer_match_20261010/summary.json`: 36 решений, 25 раздач, +48 chips (+4.8 bb), остановка «клик не подтверждён сменой состояния» | ТВ (малая выборка, вывода о силе делать нельзя). Матч владельца 11.10 в этом треке **не запускался** |

## 2. Интерфейс

| # | Требование | Доказательство | Статус |
|---|---|---|---|
| 2.1 | Лобби/стол/кнопки/HUD/логи/реплей | `src/App.jsx`, `src/tournament/Table.jsx`, `Controls.jsx`, `HandLog.jsx`, `src/replay/HandReplay.jsx` | Код; `vite build` проходит после правок |
| 2.2 | Dashboard, LeakFinder, GTOAnalyzer, DebriefScreen, Leaderboard | `src/stats/*` | Код |
| 2.3 | Десктоп Electron + Screen Parser/Overlay (снимок экрана → облачный Claude) | `desktop/main.js`, `desktop/overlay.html` | Код; **риск политики**, см. аудит §5 |
| 2.4 | PWA/Capacitor | `public/manifest.json`, `capacitor.config.json` | Код |

## 3. Правила и расчёты

| # | Требование | Доказательство | Статус |
|---|---|---|---|
| 3.1 | Оценщик 5/7 карт | `src/engine/evaluator.js`, `test/math.test.js` (полный перебор 2 598 960 рук = эталонное распределение) | ТВ |
| 3.2 | Эквити, пот-оддсы, M, SPR | `src/engine/equity.js` | ТВ после правки ничьих/CI/seed |
| 3.3 | ICM | `src/engine/icm.js`, тест против Malmuth–Harville (до 6 игроков) | ТВ (точный режим); >10 игроков — приближение без теста |
| 3.4 | Рейк кэша | `cashFormats.js` объявлял `rake/rakeCap`, **нигде не применялось** | **ИСПРАВЛЕНО** (`potSettlement.computeRake`, `App.jsx`), ТВ: 3 теста |
| 3.5 | Диапазоны (RFI/3bet/BB-defense/push-fold) | `src/data/gtoRanges.js`, `solverRanges.js`, `ranges.js` | Код; происхождение таблиц и соответствие солверу НЕ проверены |
| 3.6 | Python-движок BotLab (сайд-поты, мин-рейз, сохранение фишек) | `apps/poker-botlab/tests/test_engine.py`, `test_evaluator.py` | ТВ (весь набор зелёный) |
| 3.7 | Рейк в BotLab | `engine.Rake`, `tests/test_rake.py` | **ДОБАВЛЕНО**, ТВ |

## 4. История раздач и статистика

| # | Требование | Доказательство | Статус |
|---|---|---|---|
| 4.1 | Запись решений/экспорт сессии | `src/recorder/ActionRecorder.js` `exportSession()` | Код; формат читается `botlab report` (тест на фикстуре в этом формате) |
| 4.2 | Импорт текстовой HH (GG/PokerStars-подобный) | `src/lib/hhParser.js` | **ИСПРАВЛЕНО**: сумма «raises X to Y», позиция, VPIP; ТВ: `test/hhparser.test.js` |
| 4.3 | VPIP/PFR | 12 мест считали по-разному (парсер давал VPIP=100%) | **ИСПРАВЛЕНО**: `src/lib/statDefs.js`; ТВ: `test/stats.test.js` |
| 4.4 | WTSD/AF/c-bet и пр. | `TODO.md` BUG-2/3 помечены сделанными; `hhParser.analyzeRealHands` «showdownHands» — прокси (≥3 карт борда), не показ | ЧВ (известный недочёт, не исправлялся) |
| 4.5 | Локальная HH BotLab (`decisions.jsonl`) | `apps/poker-botlab/evidence/trainer-20261005-103030/` | ТВ (05.10) |

## 5. Симуляции

| # | Требование | Доказательство | Статус |
|---|---|---|---|
| 5.1 | Сидированная симуляция сессии на движке приложения | `tools/simulate-session.mjs`; два прогона seed=3 дали одинаковый sha256 | ТВ |
| 5.2 | Арена BotLab с CI, дубль-раздачей, seed | `apps/poker-botlab/botlab/arena.py`; `MP21_B_POKER_evidence/sim_report/` | ТВ |
| 5.3 | Браузерные воркеры `simWorker.js`, `cfrWorker.js` | `src/workers/*` | Код (не запускались) |
| 5.4 | Точный решатель реки (poker-lora) | `apps/poker-lora/pokerlora/solver.py`, 26 тестов | ТВ (перепроверено прогоном 11.10) |

## 6. Сохранение состояния

| # | Требование | Доказательство | Статус |
|---|---|---|---|
| 6.1 | Сессии/настройки/профили оппонентов в `localStorage` (до 50 сессий) | `src/lib/storage.js`, `ActionRecorder.saveSession` | Код; перезапуск в браузере не прогонялся. Хранилище привязано к браузеру/устройству |
| 6.2 | Сохранение прогона симуляции | `session_*.json`, `report.json` с sha256 входа | ТВ |

## 7. Документация запуска

| # | Требование | Доказательство | Статус |
|---|---|---|---|
| 7.1 | Запуск/тесты/симуляция Poker Train | `docs/RUN_LOCAL_RU.md` (новый), `CLAUDE.md` | ТВ (команды выполнены) |
| 7.2 | BotLab: рейк, отчёт | `apps/poker-botlab/README_RU.md` | ТВ |
| 7.3 | Workflow 8.2 | `MP21_B_POKER_WORKFLOW_8_2.md` | ТВ |

Коммерческие статусы: «принят реальным заказчиком» — **NOT PROVEN**, «оплачено» — **NOT PROVEN** (нет клиента,
договора и платежа; доказательств не существует).
