# Аудит «уток» (недоказанных утверждений) — 22.10.2026

Метод: только то, что проверяется дёшево внутри дерева (наличие файла/функции/константы, чтение JSON-улик, `grep`, `pytest --collect-only` без запуска тестов). Тесты не запускались. Статусы: `VERIFIED_IN_TREE`, `UNVERIFIABLE_FROM_TREE`, `CONTRADICTED`, `OVERSTATED`. Правило AGENTS.md: спецификация, скриншот, новый entry point и «вспомнил из памяти» не доказывают уровень.

Проверены в этом проходе: `BOSSMAN_2_6_FINAL_ACCEPTANCE_20261021.md` (A), `BOSSMAN_MOBILE_OPENCODE_ACCEPTANCE.md` (M), `BOSSMAN_UNIFIED_15X3_FINAL_ACCEPTANCE.md` (U), `BOSSMAN_ONE_STATE_20261018_RU.md` (O). **Не успели** (NOT_RUN): `README.md`, `CURRENT_STATE.md`, `production-20261021/README_RU.md`, мастер-промпты — помечены ниже.

| # | Утверждение | Источник | Что найдено в дереве | Статус |
|---|---|---|---|---|
| 1 | «Release Gate READY, PASS по всем этапам» | A:10 | `e2e_stop_holdout_receipt.json`: `tier5_status: NOT_PROVEN`; `G1`,`G2` = `SYNTHETIC`; 15x3 blind holdout, перенос весов, soak 24/48 ч = `NOT_RUN`, выручка = `BLOCKED` | OVERSTATED |
| 2 | «+40% на слепом holdout, 0.0→0.4» | A:42,104-106 | `stage6_live_holdout_proof.json`: 5 задач, 0/5→2/5; у unassisted/holdout `tool_errors: 3` из 5 попыток; `weights_unchanged: true`; уроки из памяти = coaching, не обучение | OVERSTATED |
| 3 | Замер «на локальной модели» | stage6 JSON `label` | `endpoint: https://api.groq.com/openai/v1`, `model_tested: openai/gpt-oss-120b` — облако; подпись «configured local endpoint» неверна | CONTRADICTED |
| 4 | «3 правила в LessonBook» | A:102 | JSON: `verified_lessons_after_restart: 4` | CONTRADICTED (мелко) |
| 5 | «38 заметок сохранено через рестарт», «258 ≤ 450 символов», деградация 0.0 | A:98-107 | JSON: `total_notes_persisted 38`, `compact_slice_chars 258/450`, `degradation 0.0` | VERIFIED_IN_TREE |
| 6 | «G2_sealed_holdout PASS 98/98» | A:63 | 98 = 72+9+10+7 внутридеревенных тестов, `evidence_kind: SYNTHETIC`; не слепой holdout Tier 5 | OVERSTATED |
| 7 | «Заработок 7 096,71 CZK ($322,58)» | A:53 | оферта PDF + 4 paper-сделки — не выручка; `live_proof_report.json` в дереве нет; `audited_external_revenue_documents: BLOCKED` | OVERSTATED / UNVERIFIABLE_FROM_TREE |
| 8 | Пути `.../wt-rc-20261019/...` как доказательства | A:50-142 | ссылки `file:///C:/Users/asd/Bossman/wt-rc-20261019/` вне репозитория; копии в `docs/audits/...` есть, кроме `2026-10-20-earnings-and-pc-control/` | UNVERIFIABLE_FROM_TREE (частично) |
| 9 | STOP отклик `57.75 мс`, HTTP 409, PID 24796 | A:60,116 | значения есть в `owner_acceptance_proof.json`; в receipt другой прогон: `stop_http_latency_ms 735.28` | VERIFIED_IN_TREE (два разных прогона) |
| 10 | «191 запись AuditJournal» | A:61 | строки `191` в `owner_acceptance_proof.json` нет (в receipt: `count 164`) | UNVERIFIABLE_FROM_TREE |
| 11 | Bandit B613: `DEFAULT_BASE_MODEL_REVISION="main"` в `bossman-core/bossman/SelfImprove/lora_trainer.py` | A:30 | файла по этому пути нет; `"main"` — не закрепление ревизии | CONTRADICTED |
| 12 | Лимиты $0,50 / $2,00, `COST_TIER_PRIORITY` | A:86-89 | `coding_tasks.py:632-633` `0.50`/`2.00` | VERIFIED_IN_TREE |
| 13 | «RunPod FORBIDDEN» (402/403) | A:91, M:19 | `agent_opencode.py:526` `RUNPOD_FORBIDDEN` | VERIFIED_IN_TREE |
| 14 | OutboundGuard: 1 сообщение/сутки/чат | A:68 | `command-center/bcc/pit/outbound_guard.py` существует; константа проверена не была | UNVERIFIABLE_FROM_TREE |
| 15 | «6/6 непрошеных блокировано, 0 ушло в сеть» | A:70 | прогон вживую, в дереве нет сырой записи | UNVERIFIABLE_FROM_TREE |
| 16 | Мобильный шлюз «Web Pult :8802» | M:15 | код слушает `127.0.0.1:8840` | CONTRADICTED |
| 17 | «All 15 scenarios» | M:6 | таблица и `test_01..test_16` — 16 | CONTRADICTED (мелко) |
| 18 | «zero `innerHTML`» в `app.js`/`index.html` | M:61, U:15,36 | `grep -c innerHTML` = 0 в `app.js`, `index.html` (и во всём `ui/`) | VERIFIED_IN_TREE |
| 19 | 16 тестов существуют | M:69-86 | все 16 функций найдены в `test_mobile_opencode_agent.py` | VERIFIED_IN_TREE (PASS не проверялся) |
| 20 | «254 passed» war-terminal | U:14,22,99 | `pytest --collect-only apps/war-terminal` = 254 | VERIFIED_IN_TREE (число); passed — UNVERIFIABLE |
| 21 | «45 VERIFIED IMPROVEMENTS» | U:1 | тесты названы лишь для части пунктов; прогона нет; ID `G1..G3`, `B1` повторяются в разных смыслах | OVERSTATED |
| 22 | «3% наценка» как норма | U:94 | `quote.py`: `DEFAULT_MARGIN=0.03`, `require_explicit_margin=False` по умолчанию; 20.10 RT20-02 требует явной ставки | OVERSTATED (синтетический receipt ≠ все поверхности) |
| 23 | STOP блокирует котировку (RT20-01) | redteam 20.10 | `agent.py:48` `WRITE_PATTERNS` теперь включает `RE_GIVE`,`RE_GET` | VERIFIED_IN_TREE |
| 24 | `36 passed` и `66 passed` для `test_direct_gen_resident.py` | O:59,89 | противоречат друг другу; в дереве собирается 39 тестов | CONTRADICTED |
| 25 | `3 passed` `test_companion_genmodel.py` | O:59 | collect-only = 3 | VERIFIED_IN_TREE |
| 26 | «1 898 passed (U1..U26)»; «460 passed test_autonomy_*» | O:88,91 | `-k autonomy` собирает 479 тестов; 1 898 не проверялось | UNVERIFIABLE_FROM_TREE |
| 27 | «0/39 живых PASS» | O:12 | честная формулировка, в дереве согласуется с `tree-registry.json` (0 `ci`/`owner_pc`, AGENTS.md) | VERIFIED_IN_TREE |

Не проверено (NOT_RUN): `README.md` («28/30 agent-school», «900 листьев», «35+29 тестов Poker»), `production-20261021/README_RU.md`, мастер-промпты.

Итого по 27 строкам: VERIFIED_IN_TREE 10, UNVERIFIABLE_FROM_TREE 4 (+ строка 8 частично, строка 7 смешанная с OVERSTATED), CONTRADICTED 6, OVERSTATED 5.

Пять главных: (1) `+40%` — coaching на 5 задачах с tool_errors, облако, не обучение; (2) `READY` против `tier5_status NOT_PROVEN`; (3) G1/G2 — `SYNTHETIC`; (4) «заработок» — оферта и paper, источник отсутствует в дереве; (5) `lora_trainer.py` по указанному пути не существует.

⟦ЗАПОЛНИТЬ ПРИ СБОРКЕ: результаты прогона тестов Argon из docs/audits/2026-10-22-tools-skills-proof⟧
