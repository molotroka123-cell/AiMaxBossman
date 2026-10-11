# MP21 Track D: аудит торгового бота и токен-бота (11.10.2026)

Ветка `mp/d-trading-audit-20261011`, worktree `C:\Users\asd\Bossman\wt-mp-d-20261011`, база `feat/bossman-genjutsu-jeff-unified-20261010` (250e24ec).
Режим: только чтение кода, локальные тесты, публичные документы. Ни одной живой сделки, подписи, выпуска токена, ликвидности, перевода; ключи не использовались и не печатались.

## 0. Короткий вывод

1. В репозитории есть ТРИ разные вещи, и их нельзя смешивать:
   - `bossman-core/bossman/trading_learning/` — обучающий модуль, paper/replay. Исполнения нет физически (нет клиента биржи, подписи, ключа). Дизайн честный; найдены дефекты измерения (ниже).
   - `apps/token-launch-helper/` — одноразовый помощник запуска ОДНОГО токена pump.fun с ОДНОЙ раскрытой покупкой, подпись только локально владельцем. Реальной отправки не было (в `evidence/` только `simulateTransaction`).
   - `solana_volume_suite/` + навык `.agents/skills/solana-volume-suite` + манифест `apps/solana-volume-suite` — по собственному описанию «генерация объёма», «разрыв графа кошельков (Anti-Bubblemaps)», ротация токенов между кошельками, «KOTH-пульсы для верха в Photon/BullX». Это класс wash trading / манипуляции видимостью. Не лечится «исправлением багов»; помещён в карантин (раздел 6).
2. Доказательств прибыльности нет ни у одной части. Paper-арена: лучший агент +0.68% на 1 сделке; `new_pair` −16.7% и −21.7%; кандидатов на реальные деньги «нет». Когорта creator fee: 2261 токенов увидено, замеров 0 на момент последнего отчёта.
3. Трейдинг и выпуск токенов НЕ являются безопасным способом заработка (раздел 8).

## 1. Инвентаризация (сети, версии, источники)

| Компонент | Путь | Сеть / версия | Статус |
|---|---|---|---|
| Learning-модуль | `bossman-core/bossman/trading_learning/*` (26 .py, 5389 строк) | без сети исполнения; read-only хосты `lite-api.jup.ag`, `api.dexscreener.com`, `api.mainnet-beta.solana.com` (`sol_arena.py:45-51`), GeckoTerminal (`pump_creator_cohort.py:32`) | работает, paper |
| Paper-арена (данные) | `C:\Users\asd\Bossman\paper-arena-20261009\`, `-20261010\`, `-smoke-20261009\` | реальные котировки Jupiter, виртуальные $50 | отчёты есть, см. раздел 3 |
| Когорта creator fee | `C:\Users\asd\Bossman\pump-cohort-20261009\` | pump.fun / PumpSwap, mainnet | сбор шёл, замеров 0 |
| Витрина | `command-center/bcc/features/trading_lab.py`, `bcc/market/*` (Twitch k1m6a, только сбор) | нет ордеров/бирж | read-only (`market_metrics.py:11`) |
| Token helper | `apps/token-launch-helper/` | Solana mainnet (RPC `api.mainnet-beta.solana.com`), `@pump-fun/pump-sdk 4.0.0`, `@solana/web3.js 1.98.2`, `bn.js 5.2.2` (`package.json`) | прототип; `node_modules` не установлены, JS-тесты в этой среде не запускались |
| Исследование SDK | `C:\Users\asd\Bossman\token-launch-research\` (tgz sha256 `47c80817...fbd29`, распакован в `package/`, IDL `src/idl/pump.json`, `pump_amm.json`, `pump_fees.json`) | pump program `6EF8rrec...F6P`, pump_amm `pAMMBay6...`, pump_fees `pfeeUxB6...` | источник правды для сверки |
| Volume suite | `solana_volume_suite/` (29 .py), CI `.github/workflows/solana-safety-ci.yml`, CODEOWNERS | Solana mainnet-beta, Jito block engine, Jupiter `quote-api.jup.ag/v6`, Raydium | карантин |
| Копии | `apps-local/.../bossman-poker/...` и другие worktree | те же файлы | дубликаты, не отдельные боты |

Отсутствует (не угадывалось): смарт-контракты (нет `.sol`, `.rs`, `Anchor.toml`, Hardhat/Foundry), собственная программа токена, скрипты деплоя контрактов, биржевые API-клиенты (ccxt, Binance, Bybit, Hyperliquid и т.п.), файлы ключей/keypair в репозитории и в проверенных локальных папках, конфиги живого бота, мониторинг/алертинг боевого бота. Токен-бот = только `token-launch-helper` (создание через официальную программу pump.fun, не свой контракт). Других площадок кроме pump.fun / PumpSwap / Raydium / Jupiter / Jito в коде нет. EVM/Ethereum не используется.

Секреты: Solana-ключей и приватных данных в отслеживаемых файлах не найдено. Единственное совпадение шаблона «массив из 64 чисел» — тестовая фикстура `apps/social-farm/.../error_media_invalid.json` (не ключ). Адрес плательщика симуляции в `apps/token-launch-helper/evidence/mainnet-simulation.json` публичный, не секрет. Файлы `wallets_encrypted*.json` и `.env` в игнор-листе (`.gitignore:47,52`).

## 2. Найденные проблемы, ранжированные по риску

Условные обозначения статуса: DONE = исправлено в этой ветке с тестом; COORD = правит координатор в `wt-lora-workflow` (по сообщению владельца, я `solana_volume_suite` не трогал); OPEN = рекомендация.

### CRITICAL

**C1. Volume suite реализует искусственный объём и сокрытие связей кошельков.**
- `solana_volume_suite/core/ai_orchestrator.py:144-150` — системный промпт: «volume generation avoiding cluster detection ... Never choose round numbers»; `:67-77` режим `KOTH_PULSE`: «firing green micro-pulse for Photon/BullX top rank»; `:55-65` `FLOOR_DEFENSE` — откуп просадок («floor defense»).
- `core/funding_router.py:196-199` («funding via CEX completely severs the on-chain graph»), `:226-233` («Anti-Bubblemaps Cascade Engine»), `:422-458` `register_rotation_step` — план «купил кошельком A, продал через B, перевёл C» с задержкой блоков (круговая торговля между своими кошельками).
- `orchestrator_loop.py:163`, `treasury_guard.py:474-475,425-429` («total_volume_generated_usd»), `setup_mainnet.py:108-120` (инструкция «to break on-chain clustering»).
- `SKILL.md` (до карантина) и `app.manifest.yaml` обещали «Autonomous ... volume generation», «Poisson & Pareto ... non-round numbers» — маскировка под органику.
- Почему это критично: цель системы — ввести в заблуждение других участников рынка (видимый объём/ранг/«независимые» держатели). Это то, что запрещено правилами владельца и master-prompt. Правовой и репутационный риск (в ряде юрисдикций такие схемы квалифицируются как манипулирование рынком); не юридическая консультация.
- Статус: **DONE (карантин на уровне агентов)**: манифест `status: quarantined`, права `network.*`/`wallet.*` = `deny`, навык переименован в «QUARANTINED, do not invoke» без триггерных фраз. **OPEN для владельца**: решить удалять ли код; рекомендую удалить `funding_router.py` (cascade/rotation), `ai_orchestrator.py` режимы KOTH/FLOOR_DEFENSE и инструкции в `setup_mainnet.py`, оставить `liquidity_gate.check_liquidity`, `key_vault`, `treasury_guard` как нейтральные куски. Я не менял `solana_volume_suite/` из-за запроса координатора.

### HIGH

**H1. Уничтожение хранилища ключей (потеря средств/ключей).**
- `solana_volume_suite/dashboard/safety_app.py:286-294`: `/api/vault/generate` принимает пароль ≥ 6 символов, затем `os.remove(vault_path)`, а `vault.py:83` требует ≥ 12 символов → при пароле 6–11 символов старый vault удалён, новый не создан (ValueError, 500).
- `orchestrator_loop.py:93-98`: при ЛЮБОЙ ошибке расшифровки (в том числе неверный пароль/опечатка) vault удаляется `os.remove` и генерируется новый. Опечатка в пароле = безвозвратная потеря всех под-кошельков.
- Статус: OPEN/COORD (файлы в `solana_volume_suite`). Фикс: переименовывать в `.bak-<ts>`, не удалять; согласовать длину пароля 12 на всех входах; требовать явное подтверждение с именем файла.

**H2. Мастер-пароль vault записывается открытым текстом в `.env`.**
- `solana_volume_suite/setup_mainnet.py:127,133`: `VAULT_MASTER_PASSWORD={master_pass}` → `.env` лежит рядом с `wallets_encrypted.json`; шифрование AES-GCM теряет смысл (оба файла рядом). Маркер `ci-secret-scan: allow` на строке 127 глушит сканер.
- Статус: OPEN. Фикс: не писать пароль на диск; брать из менеджера секретов/DPAPI (в проекте есть vault, см. память `vault-never-pushed`), в `.env` — только ссылка.

**H3. Контрольная плоскость без аутентификации, «SAFETY_ONLY» обходится.**
- `safety_app.py:183-184,199-200`: `/api/bot/start` и `/api/bot/sweep` отказывают только при ПУСТОМ теле; любой непустой JSON (`{"x":1}`) запускает цикл (`:186-189`).
- `:219-227` `/api/orchestrator/start` и `:251-271` `/api/sweep` без проверок вообще; нет токена/секрета. Хост 127.0.0.1 (`run_dashboard.py`, `start_prototype.py:85`) смягчает, но любой локальный процесс/страница в браузере может вызвать POST.
- Тест `tests/test_solana_safety.py` проверяет только `/api/bot/*` с пустым `{}` — обход не покрыт; `solana_volume_suite/tests/test_e2e_prototype.py::test_dashboard_interactive_endpoints` в чистом окружении КРАСНЫЙ (500: «Vault password must contain at least 12 characters», воспроизведено), и CI его не запускает (`solana-safety-ci.yml` гоняет только два файла).
- Статус: OPEN/COORD.

**H4. Устаревшая/неверная сборка инструкций pump.fun и Jupiter, подпись данных внешнего API без проверки.**
- `stages/pumpfun_engine.py:289-302` buy содержит 12 аккаунтов (включая `SYSVAR_RENT`), а IDL SDK 4.0.0 (`token-launch-research/package/src/idl/pump.json`) требует 16: `global, fee_recipient, mint, bonding_curve, associated_bonding_curve, associated_user, user, system_program, token_program, creator_vault, event_authority, program, global_volume_accumulator, user_volume_accumulator, fee_config, fee_program`. Нет `creator_vault`, `global_volume_accumulator`, `user_volume_accumulator`, `fee_config`, `fee_program`. Sell (`:318-331`, 12) против IDL (14) — тот же класс расхождения, плюс лишний `ASSOCIATED_TOKEN_PROGRAM_ID` и другой порядок. Дискриминаторы buy/sell совпадают с IDL (проверено: `66063d1201daebea`, `33e685a4017f83ad`).
- `:252-254` `PUMP_FEE_RECIPIENT` захардкожен; SDK берёт случайного из `global.feeRecipient + global.feeRecipients` (`fees.ts:270-277`), публичные доки pump: «fee_recipient is one of the 8 fee_recipients». Актуальность одиночного адреса не проверена (нужен read-only RPC).
- `:247,276` `TOKEN_PROGRAM_ID` (legacy) для ATA, а `create_v2` создаёт токены Token-2022 (в `evidence/mainnet-simulation.json` логи `TokenzQdBNbLq...`). ATA будет выведен неверно.
- `:376-377` «≈30M токенов за 1 SOL» — фиксированная оценка вместо расчёта по виртуальным резервам кривой; `max_sol_lamports = slice*1.05` не связан с реальной ценой.
- Практический эффект сейчас: транзакции не уйдут (`jito_client.py:289-293` `send_bundle` всегда бросает `LIVE_EXECUTION_DISABLED`), т.е. это не потеря денег, а недостоверный «рабочий» код. Но он подписывает: `core/jupiter_engine.py:357-370` и `stages/raydium_jupiter_engine.py:100-108` подписывают `swapTransaction`, полученную из внешнего API, не проверяя программы/получателей/payer.
- `raydium_jupiter_engine.py:11-12`, `core/jupiter_engine.py:310-311`: `quote-api.jup.ag/v6`. Документация Jupiter помечает Metis Swap API как «no longer actively maintained», рекомендует Swap API V2; формат `priceImpactPct` из этой выборки подтвердить не удалось (страница не отдала) — OPEN.
- Статус: **COORD** (хардненинг подписи PumpFunEngine/JupiterSwapEngine/`compile_v0_transaction` делает координатор в wt-lora-workflow). Для него: либо заменить ручную сборку на официальный SDK/IDL с тестом на соответствие IDL, либо удалить.

**H5. Фальшивое «подтверждение on-chain» и вымышленные показатели.**
- `orchestrator_loop.py:176-179`: `sig = "sim_jito_sig_..."`, затем `decision.confirmed_onchain = True` — поле, чей инвариант в `ai_orchestrator.py:19` «True ONLY if confirmed on-chain», выставляется для симуляции. События `TRADE_EXECUTED` (`:187`) выглядят как реальные.
- `orchestrator_loop.py:105-108`: стартовые балансы кошельков выдуманы (`0.42 + (idx%4)*0.18` SOL). `safety_app.py:104`: `"mempool_leak_prevention": "100%_SECURED"` без измерения. `liquidity_gate.py:114-129` (inner) при ошибке DexScreener тихо подставляет «650 SOL / $117k» мок-резервы, а `last_status` инициализирован `"PASS"` (`:77`); `fetch_dexscreener_reserves` подменяет отсутствующие поля `or 120000.0 / 650.0`. Пользователь видит PASS на выдуманных данных.
- Статус: OPEN/COORD.

### MEDIUM

**M1. Двойной счёт издержек в EV (метрики).** `bossman-core/.../metrics.py:152-155`: `wr*avg_win - lr*avg_loss` уже равно среднему `net_pnl` (а `net_pnl` в `models.py` вычитает fees/funding/slippage), затем fees/funding/slippage вычитаются ещё раз; то же в `out_of_sample_ev` (`:178-179`). Ошибка консервативная (не рождает ложную «прибыльность»), поэтому гейт не ломает, но «expected_value» не равен среднему чистому P&L и `cost_sensitivity.gross_ev` (`:190`) искажён. Исправление ослабит гейт PROFITABLE_CANDIDATE, поэтому НЕ менялось без решения владельца. Тест на текущее поведение не добавлялся.

**M2. Оптимистичное исполнение стопа в бэктесте (DONE).** `backtest.py:386-392`: стоп исполнялся по цене стопа даже когда бар открылся ниже/выше стопа (гэп). Теперь исполняется по open бара (хуже). Тесты `tests/test_trading_mp21_d_fixes.py::test_long_stop_gap_fills_at_open_not_at_stop`, `::test_short_stop_gap_fills_at_open`.

**M3. Нет проскальзывания на выходе, спреда, частичных исполнений, плеча.** `paper.py:271-277`: на выходе только комиссия, проскальзывание берётся только со входа (`:259`); `CostModel` (`metrics.py:24-30`) не имеет спреда; fill всегда 100% (`submit`), плечо не моделируется (ликвидации не считаются). Для paper это завышает результат стратегий; для Solana-арены компенсировано реальными котировками. OPEN. Дополнительно `paper.py:252-254` проверка «слишком хороший fill» только для LONG (для SHORT нет), и клэмп цены в бар (`:248`) может обнулить заявленное проскальзывание.

**M4. Cohort: отравленная очередь и неверная «первая сутки» (DONE).** `pump_creator_cohort.py:105-127`: исключение при запросе одного токена (например 404) прерывало цикл ДО `_write`, поэтому токены позади в очереди не замерялись никогда, а уже замеренные в проходе терялись. Плюс `h24` = ПОСЛЕДНИЕ 24 ч на момент замера: при бэклоге (опрос ≈25–33 новых токена за 5 мин против `max_n=25` за цикл) замер выполняется на возрасте ≫ 24 ч и не описывает «первые сутки». Исправлено: per-token try/except с пометкой `unmeasurable`, флаг `first_day_window` (возраст ≤ 26 ч), `summarize` исключает поздние замеры и считает незамеренные. Тесты `::test_one_failing_token_does_not_block_or_discard_the_cohort`, `::test_late_measurement_is_excluded_from_first_day_stats`.

**M5. Ложное утверждение о полноте выборки (DONE).** Отчёт когорты заявлял «ВСЕ новые пулы pump.fun». Код читает не более 3 страниц `new_pools` за опрос (`poll(pages=3)`), покрытие не измерено. Текст исправлен. Это важно: вывод «создатель в среднем зарабатывает X» без полного покрытия нельзя выдавать за факт.

**M6. Token helper: раскрытие dev-buy не гарантировано при `--uri`.** `apps/token-launch-helper/src/cli.js:164-167` дописывает «Dev buy: X SOL from <wallet>» в metadata только в пути `--upload`; с `--uri` раскрытие в чужом/ранее залитом JSON не проверяется (`launch.js:69-73` лишь форматирует строку, в инструкции создания описания нет). Также `launch.js:113-121` `send()` проверяет `simulation.err === null`, но не связывает симуляцию с конкретной транзакцией (подойдёт любой объект с `err:null`), `:109` симуляция идёт с `replaceRecentBlockhash:true` (не байт-в-байт та транзакция), запрет `example.invalid` URI только в CLI (`cli.js:174`), не в `send()`. Ключ владельца читается из открытого JSON-файла (`cli.js:175`). OPEN, рекомендации в разделе 6.

**M7. Нет единой точки исполнения и стоп-автомата для живых режимов.** В learning-модуле это правильно (execution_mode всегда OFF, `safety.py:51-58`, нет клиента), но `FORBIDDEN_ACTIONS` (`safety.py:33-37`) — denylist: действия `swap`, `send_transaction`, `sign`, `create_order` не перечислены, и проверка действия без `stage` пропускает неизвестное. Защита держится на физическом отсутствии клиента, не на списке. LOW для learning, но для любого будущего живого модуля нужен allowlist.

### LOW / замечания

- L1. `treasury_guard.py:557-559` `is_within_budget()` не учитывает Jito-чаевые и сетевые комиссии, а `:547-553` останавливает по `total_burn` (с чаевыми) флагом `is_circuit_breaker_tripped`, который цикл не читает (`orchestrator_loop.py:152`). Два разных определения «лимита убытка $40». Плюс `:594-597` `reset_circuit_breaker(new_limit_usd=50)` — лимит можно поднять программно без владельца (нарушает правило «агент не расширяет свои финансовые полномочия»).
- L2. `liquidity_gate.py` (inner `:166-182`): `SLICED_PASS` возвращает `execution_allowed: True`, тогда как новый `check_liquidity` намеренно «не авторизует исполнение» и тест `test_large_order_cannot_bypass_total_impact_by_splitting` запрещает дробление. Два пути с противоположной политикой.
- L3. `jito_client.py:169-178` список tip-аккаунтов захардкожен; Jito публикует их динамически (getTipAccounts). Не проверялось на актуальность.
- L4. `requirements.txt` pin `==` без хешей (сам файл это признаёт); версия `cryptography==50.0.1` и др. не верифицировалась мной.
- L5. Арена: `promotion_candidates` (`sol_arena.py`) допускает кандидата при ≥30 сделок и капитале выше обоих эталонов, без доверительного интервала, OOS и без поправки на выбор из 5 стратегий. Вселенная токенов строится из `token-profiles` и `token-boosts` DexScreener (`:113-114`) — это платная реклама токенов, смещение отбора. Котировка ≠ исполнение: нет вероятности попадания транзакции, MEV, Jito-чаевых. Отчёт честно пишет «Реальных сделок нет».
- L6. Артефакт измерения: в `paper-arena-20261009\REPORT.md` `hold_sol` показан «НЕТ, $1.0, −98%». Это не результат рынка: один `no_route` по SOL обнулил позицию (код `sol_arena.py:tick` уже содержит комментарий об этом и исправление). Эти числа нельзя использовать как доходность.

## 3. Аудит торгового контура по пунктам master-prompt

| Пункт | Learning/paper модуль | Volume suite | Token helper |
|---|---|---|---|
| Сигналы | правила `strategy.py`, claims с верификацией; арена: пороговые правила по снимку DexScreener (`sol_arena.py:183-188`) | LLM/детерминированный «объём», не сигнал | нет |
| Поток ордеров | `PaperBroker.submit` с задержкой ≥1 бар, дубль по `client_order_id` (`paper.py:235-236`) | симуляция, `send_bundle` всегда отказ | 1 транзакция create_v2 + 1 buy (`launch.js:75-89`) |
| API-права | только публичные read-only хосты, allowlist хостов (`sol_arena.py:79-80`) | Jupiter/Jito/Dex; манифест до карантина `network.jito: allow`, `wallet.signing: ask` | RPC + pump.fun IPFS (при `--upload`) |
| Хранение ключей | ключей нет | AES-256-GCM PBKDF2 100k, но пароль в `.env` (H2), удаление vault (H1) | JSON keypair в файле, читается при `--send` (`cli.js:175`), `*.keypair.json` в .gitignore |
| Размер позиции | `size_frac` 0.2–0.3 от капитала, `max_positions 2` | `$40` лимит убытка, 0.04–0.45 SOL | потолок dev-buy 5 SOL (`launch.js:48`) |
| Плечо | не моделируется | нет | нет |
| Лимиты риска | `death_floor_usd 2.5`, `write_off_ticks 30` | circuit breaker (L1) | нет, один запуск |
| Комиссии/спред/проскальзывание | сеть+приоритет+рента+котировка с price impact; спред в `CostModel` нет (M3) | pumpfun 1% / raydium 0.25% константы, `treasury_guard.py:523` | SDK, slippage=1% внутри SDK |
| Задержка | 2 с повторная котировка, падение при ухудшении > допуска (`sol_arena.py` swap) | `delay_sec` 2–95 | нет |
| Дубли/частичные | дубли — да; частичные fill — нет | нет | один раз, нет повторов (`cli.js:142`) |
| Остановка | `safety.py` постоянный OFF; арена — death floor | `/api/bot/stop`, `orchestrator.stop()` (локально, без auth) | `--send` + фраза + симуляция |
| Мониторинг | `journal.jsonl`, `REPORT.md`, `state.json` | события в памяти (300 шт) | stdout |
| Перезапуск | `state.json` восстанавливается (`Arena._load`) | всё в памяти, vault риск H1 | без состояния |
| Бэктест/paper отдельно от реальной доходности | да: `EvidenceClass`, `assert_live_proof` требует полей реального исполнения (`safety.py:143-152`) | «PAPER_TRADING» в UI, но метрики «volume_generated» без пометки о фиктивности | н/д |

Реальная доходность: реальных сделок нет ни в одной части, поэтому «реальная доходность» не определена. Paper-результаты арены (после издержек, по реальным котировкам): шаг 51 (10.10): flow_matrix +0.68% (1 сделка), momentum +0.53% (2 сделки), new_pair −16.73% (2 сделки); шаг 228 (09.10): momentum −5.04% (7), new_pair −21.72% (19 сделок). Вердикт арены: кандидатов на реальные деньги нет. Выборки малы, это не доказывает ни прибыльность, ни убыточность стратегии в целом.

## 4. Токен-бот: проверка полномочий и условий

Метод: код helper + IDL/SDK 4.0.0 + публичные документы. Ончейн-состояния реально выпущенных токенов не читалось (RPC не вызывался), поэтому пункты «факт на mainnet» отмечены как НЕ ПРОВЕРЕНО.

| Параметр | Что установлено | Источник |
|---|---|---|
| Программа | pump `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`, mainnet | `pumpfun_engine.py:252`, IDL `pump.json` address |
| Создание | `create_v2` (Token-2022, MetadataPointer, TokenMetadata в логах симуляции) | `mainnet-simulation.json` |
| Mint authority | в IDL аккаунт `mint_authority` — PDA программы. Кто и когда отзывает право mint после создания — НЕ ПРОВЕРЕНО on-chain | IDL `create_v2` accounts |
| Freeze authority | НЕ ПРОВЕРЕНО (в логах симуляции не отражено). По документации SPL: если freeze authority = None — отключено навсегда | solana-program.com/docs/token |
| Update authority метаданных | НЕ ПРОВЕРЕНО on-chain; у Token-2022 metadata pointer есть update authority, которая может менять имя/URI | логи симуляции (`TokenMetadataInstruction: Initialize`) |
| Эмиссия/распределение | задаёт программа pump (фиксированная кривая); helper лишь одна покупка dev-buy ≤ 5 SOL, раскрытая в описании | `launch.js:48,70-73` |
| Комиссии | кривая: protocol 95 bps + creator 30 bps; PumpSwap: тиры creator 30–95 bps → 5 bps; снимок mainnet 2026-10-09 | `pump_fee_tables_20261009.json` |
| Создатель получает | creator fee по ставке выше; самоторговля платит все комиссии (~1.25%) ради ~0.30% | `pump_creator_cohort.py` докстринг |
| Владельцы | creator = wallet владельца (`launch.js:83`); SDK 4.0.0 имеет новые опции `creatorFeeBps`, `holderReward`, `isTokenizedAgent`, `buyBackBps` — helper их не выставляет (значения по умолчанию) | `sdk.ts:1540-1573` |
| Ликвидность/блокировки | ликвидность — внутренняя кривая программы; после миграции — PumpSwap. Блокировок (locks) и vesting в helper нет | IDL, README SDK |
| Внешние вызовы | pump.fun IPFS `https://pump.fun/api/ipfs` (при `--upload`), RPC | `cli.js:152` |
| Возможность менять условия | глобальные параметры (fee_recipients, fee tiers, creator_fee) меняются администратором программы pump.fun, не создателем токена; флаги `cashback` — устарели, создать нельзя (SDK `@deprecated cashback coins can no longer be created (6082)`) | `sdk.ts:1567`, публичные доки pump |

Сверка с публичной документацией: `pump-public-docs` (PUMP_PROGRAM_README) перечисляет 8 fee_recipients в `Global`, поле `creator` в `BondingCurve`, отмечает `withdraw` как отключённый и cashback как deprecated, но НЕ содержит полных списков аккаунтов buy/sell и не описывает Token-2022/create_v2. Поэтому основой сверки служит IDL из SDK 4.0.0 (локальный tgz). Совпадение: дискриминаторы buy/sell; расхождение: списки аккаунтов в `pumpfun_engine.py` (H4). Документация Jupiter: Metis Swap API не поддерживается активно (H4). Документация SPL: mint/freeze authority отзываются необратимо (`SetAuthority` → None).

Риски создателя токена, которые код не закрывает и не должен «обходить»: dev-buy раскрыт только в тексте описания; любое последующее действие владельца (продажа крупной доли, смена метаданных) на кривой видимо для рынка и может быть воспринято как rug. Помощник сам по себе обмана не содержит.

## 5. Что исправлено в ветке

| Файл | Изменение | Тесты |
|---|---|---|
| `bossman-core/bossman/trading_learning/backtest.py` | гэп через стоп исполняется по open бара | `bossman-core/tests/test_trading_mp21_d_fixes.py` (2 теста) |
| `bossman-core/bossman/trading_learning/pump_creator_cohort.py` | устойчивость к ошибке токена, окно первых суток, честная формулировка выборки | там же (2 теста) |
| `apps/solana-volume-suite/app.manifest.yaml` | `status: quarantined`, `network.*`/`wallet.*` = deny | `tests/test_mp21_d_volume_suite_quarantine.py` (2 теста) |
| `.agents/skills/solana-volume-suite/SKILL.md` | описание без триггеров, баннер карантина | там же |

Прогон (по одному файлу, без -n): `test_trading_mp21_d_fixes` 4 passed; `test_trading_pump_creator_cohort` 3 passed; `test_trading_paper_memory` 30 passed; `test_trading_lookahead` 14 passed; `test_mp21_d_volume_suite_quarantine` 2 passed. До правок полный набор `bossman-core/tests/test_trading_*.py` давал 156 passed, 4 skipped. `command-center/tests` не запускались (нет `sqlalchemy`); JS-тесты helper не запускались (нет `node_modules`, ничего не устанавливалось по сети кроме Python-пакетов во временный venv). Baseline `solana_volume_suite/tests` + `tests/test_solana_safety.py`: 54 passed, 1 failed (`test_dashboard_interactive_endpoints`, существовал до моих правок).

Манифестный тест командного центра `test_apps_manifest_probe_paths.py` ожидает `MANIFEST_ONLY = {"solana-volume-suite"}`; поля `status`/`permissions` он не читает, но запустить его здесь не удалось.

## 6. Список исправлений (рекомендации по приоритету)

1. Владельцу: решение об удалении `funding_router.py`, режимов KOTH/FLOOR_DEFENSE, текстов «Anti-Bubblemaps» (C1). До решения карантин остаётся.
2. COORD (wt-lora-workflow): подпись — не подписывать ничего, полученное из внешнего API, без проверки программ/аккаунтов; привести buy/sell к IDL либо удалить `pumpfun_engine.py`; Token-2022 для ATA; заменить Jupiter v6 или удалить (H4).
3. Vault: не удалять, а архивировать; единая минимальная длина пароля 12; не писать пароль в `.env` (H1, H2).
4. Контрольная плоскость: токен/секрет на POST, 403 безусловно в safety-only режиме; тесты на непустое тело и на `/api/orchestrator/start`, `/api/sweep`; добавить `test_e2e_prototype.py` в CI или исправить (H3).
5. Убрать `confirmed_onchain=True` для симуляции, фиктивные балансы, мок-резервы с PASS; не отдавать «100%_SECURED» (H5).
6. Метрики: после решения владельца убрать двойной счёт издержек в EV и добавить выходное проскальзывание, спред, частичные исполнения (M1, M3).
7. Token helper: проверять раскрытие в загруженном metadata JSON при `--uri`; связать симуляцию с хешем сообщения; запрет placeholder URI внутри `send()`; ключ читать из защищённого хранилища; перед любым реальным использованием — read-only проверка authority созданного минта (M6).
8. Арена: добавить доверительный интервал/OOS в `promotion_candidates`, исключить платные boosts из вселенной или пометить их, учесть вероятность попадания транзакции (L5, L6).
9. Cohort: измерить полноту выборки (сравнение с независимым счётчиком запусков), увеличить `max_n`/частоту, чтобы бэклог не копился (M4, M5).

## 7. Что НЕ проверено (честно)

- Ончейн-состояние: актуальность `PUMP_FEE_RECIPIENT`, authority реальных токенов pump (mint/freeze/update), актуальный формат `priceImpactPct` Jupiter, актуальность Jito tip-аккаунтов — не проверялись (нет read-only RPC-вызовов, чтобы не создавать нагрузку и не выходить за рамки).
- JS-тесты helper и Python-тесты command-center в этой среде не запускались.
- Я не оценивал юридический статус; формулировки о манипулировании — оценка дизайна, не юридическое заключение.

## 8. Note: торговля и выпуск токенов — НЕ безопасный способ заработка

- Нет ни одного доказательства положительной ожидаемой доходности в этом проекте: paper-арена после издержек не выдала кандидатов; когорта creator fee не набрала замеров; сама когорта в код-комментарии признаёт, что самоторговля создателя убыточна (платит ~1.25% за ~0.30% обратно).
- Статистический барьер в проекте верный (`metrics.py:16-19`: ≥30 сделок, ≥10 вне выборки, ≥2 режима, EV>0 после издержек), и ни одна стратегия его не прошла. «Прибыльная» без прохождения барьера — недопустимая формулировка.
- Выпуск токена: создатель принимает на себя риск репутации, налогов и регулирования; единственный предсказуемый денежный поток — комиссия создателя, зависящая от чужого объёма, которого нет гарантии. Накрутка этого объёма — манипуляция, что запрещено и в этом проекте.
- Рабочее правило для workflow Bossman: торговый и токен-контур остаётся read-only/paper; любое действие с реальными деньгами (подпись, отправка, ликвидность, перевод) только вручную владельцем, с отдельным подтверждением непосредственно перед действием, и только после прохождения гейтов доказательности. Агент не повышает себе лимиты и не отключает журналирование.
