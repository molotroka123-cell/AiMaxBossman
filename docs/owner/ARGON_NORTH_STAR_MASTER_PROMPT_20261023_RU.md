# ARGON — NORTH STAR 2026-10-23: ЗАКРЫТЬ ОСТАВШИЕСЯ ЛИСТЬЯ И СОБРАТЬ ОДНОГО BOSSMAN

**Владелец попросил:** прочитать этот checkpoint из GitHub 23 октября 2026, не из истории чата; провести строго доказательное закрытие всех оставшихся листьев всех 15 направлений и собрать одно ядро Bossman с реальным самосозданием навыков через терминал.

**READ FIRST (строго в таком порядке):**
1. [AGENTS.md](../../AGENTS.md) — ограничения владельца и реальные permissions.
2. [CHECKPOINT_20261022_01_AUDIT_15_RU.md](CHECKPOINT_20261022_01_AUDIT_15_RU.md) — независимый аудит 15 направлений.
3. [NORTH_STAR_LEAF_INDEX_20261022.json](NORTH_STAR_LEAF_INDEX_20261022.json) — индекс **всех 912 дочерних узлов, включая 8 retired**, стабильные IDs и source paths. Активных 904, но это не 904 багов: статус и доказательность проверяются индивидуально.
4. [CHECKPOINT_20261022_02_FRESH_OSS_RU.md](CHECKPOINT_20261022_02_FRESH_OSS_RU.md) — релизы OSS за последнюю неделю, источники и PoC gates.
5. [BOSSMAN_UNIFIED_15X3_FINAL_ACCEPTANCE.md](BOSSMAN_UNIFIED_15X3_FINAL_ACCEPTANCE.md) и [BOSSMAN_MOBILE_OPENCODE_ACCEPTANCE.md](BOSSMAN_MOBILE_OPENCODE_ACCEPTANCE.md) — claims Argon, не переносить их PASS без проверки.
6. [MP21_NORTH_STAR_TREE_20261011.md](../archive/2026-10/owner/MP21_NORTH_STAR_TREE_20261011.md), [BOSSMAN_15_VECTORS_45_STEPS_20261019_RU.md](BOSSMAN_15_VECTORS_45_STEPS_20261019_RU.md), `command-center/bcc/capability_tree_seed.json`, `tools/tree_registry_sync.py`, `tools/tree_apply_evidence.py`.

## Нулевое правило

Обнови `origin/main`, exact SHA live runtime, workflow CI, active processes, installed version. Не считать SHA из этого документа текущим автоматически: baseline audit `558780932430d2007067d724765269d050ece5d7`.

**Цель НЕ «покрасить 904 листа». Цель — каждый отдельный лист либо доказанно закрыть, либо получить конкретный воспроизводимый блокер с task, owner action, зависимостями и следующей проверкой.** Ни один лист без owner-PC evidence не становиться VERIFIED_OWNER. Исторические `working`, `reported`, `recorded` — не доказательство автономности.

## North Star как один продукт

Mobile iPhone/PWA и desktop/Telegram → ONE Command Center API → ONE task/session ledger + permission engine + STOP → ONE model router (Gemma/Qwen/local first, free cloud, paid gated) → OpenCode + Windows terminal + browser/PC tools + MCP → independent verifier + immutable receipts + memory + skill registry → тот же чат.

Сохрани только один runtime, одну очередь, одну память с provenance, один tool registry, один system of record для денег и разрешений. Всё старое через adapters, не rewrite. Jeff может оставаться paused из-за инцидента; не включать без отдельного подтверждения. RunPod только с отдельным разрешением.

## Работа с КАЖДЫМ листом

Прочитай `docs/owner/NORTH_STAR_LEAF_INDEX_20261022.json` целиком как входной manifest. Для КАЖДОГО id:

1. Resolve stable id и точный existing source path, если он изменился — update reference и сохранение старого указателя.
2. Определи: RETIRED / ALREADY_VERIFIED_ON_CURRENT_SHA / CODE_PRESENT / TEST_ONLY / MISSING / BLOCKED / NOT_RUN.
3. Отметь реальные зависимости на другие IDs, дублирование, стоимость, риски и benefit. Устаревшую или дублирующую функцию можно **закрыть через deprecation с доказательством**, не писать лишний код.
4. Для дефекта: сначала RED failing test, затем минимум исправления, GREEN, regression, integration.
5. Для runtime функций: exact source SHA, deployed SHA, action/test/receipt, Windows owner acceptance. CI зелёный только на том же candidate SHA.
6. Для нового skill: source license, skill manifest JSON Schema, test sandbox isolation, negative test, holdout re-use.
7. Для blocker: конкретный внешний шаг, владелец/железо/ключ, почему не выполнено, что можно делать безопасно без него.
8. Запиши машинно проверяемый `leaf_id -> source_sha -> tests -> ci_run_url -> owner_pc_receipt -> verdict`, без секретов и персональных данных.

У каждого активного ID должен быть entry; после обработки assert missing=0, duplicate=0. Это доказательство **полноты инвентаря**, не полноты выполнения. Значения нельзя выдумывать.

`retired` сохраняют архивный index и не переводятся в зелёный.

### 15 логических зон, порядок критичности

Jeff, UX, Apps, Models, Cloud, Computer, Media, Memory, Agents, Skills, Plugins, Ops, OSS, Commerce, Devices.

Сначала общие P0 основы (Ops / Agents / Computer / UX / Skills / Security), затем всё остальное. 15 зон ≠ 15 новых ботов или 15 long-lived веток.

## Практическое доказательство: Bossman сам создаёт навык через терминал

Создай безопасный эталон **настоящей новой способности**, не добавляй hardcoded answer:

**Миссия от владельца с iPhone:** «Боссман, у тебя нет инструмента, который по git diff готовит короткий отчёт затронутых модулей. Сам придумай skill, реализуй, протестируй, зарегистрируй и докажи, что он работает в другой ветке».

Ожидаемый observable end-to-end:

1. Пользователь послал задачу, один `task_id`.
2. Bossman распознал gap в реестре capabilities. Проверил, что уже существующий skill не решает её.
3. Сам сгенерировал короткую спецификацию, `SKILL.md`, typed schema и python worker (или иной необходимый минимальный код) **в изолированном git worktree**, используя разрешённый Web Terminal и coding tools.
4. Сгенерировал независимые fixtures, отдельный RED-test на пустой/non-existing skill; затем GREEN.
5. Запустил реальную команду в терминале, получил stdout/stderr/exit code.
6. Skill только читает git diff; запрещены изменение файлов, сеть, секреты, произвольный shell в skill.
7. Независимый reviewer проверил source, license, sandbox и тесты; настоящий hash receipt; owner разрешил promotion.
8. Runtime skills registry увидел новый skill **после restart** без ручной правки.
9. Bossman сам выбрал и применил этот skill к **другому, заранее не виденному git diff**; сравни с baseline: correctness, latency, token/$, owner interventions.
10. Испытать невалидный diff, отсутствие git и остановку STOP: корректные отказы.
11. Сверить task id и все логи между Mobile → OpenCode → PTY → verifier → memory.
12. Проверить rollback: неуспешный skill автоматически НЕ попадает в production.

**PASS** только если шаги 1–12 подтверждены реальными receipts на exact SHA. Тест с моками, заранее созданный вручную skill или описание «я создал» = НЕ PASS.

Далее: 3 независимые полезные задачи/skill cycles подряд, blind transfer на новых входах, 24-часовая стабильность и 48-часовой soak — только после P0.

## Главные blockers предыдущего аудита

- Несовпадение `main` / live: установленный runtime проверять по exact commit hash.
- Неполные/отменённые CI после быстрых пушей; один зафиксированный candidate и full required CI.
- `AUTO bypass_permissions`: auto не может обойти STOP, T2/T3, внешние сообщения, платежи, продакшен-сборку, key vault, опасные команды; negative tests на косвенный tool-call.
- Jeff outbound incident 19.10: не включать poller без RCA и owner decision.
- Историческое North Star Ladder достигло лишь инфраструктуры, не валидировано 3 цикла/self-generated skill.
- Missing/stale source paths из старого дерева: resolve или явно keep MISSING.
- Контекстное обучение != model fine-tune; измерить unseen task transfer.
- Не считать Prozorro/War Terminal автоматическим заработком; юридические проверки и owner approval обязательны.

## Список свежих OSS PoC

Смотри `CHECKPOINT_20261022_02_FRESH_OSS_RU.md`. Кандидаты: LangGraph 1.2.16 (resume correctness), Browser Use 0.13.13 (DPI screenshots), LiteLLM 1.106.2 (cost accounting), OpenCode 1.19.3 (routing variants), ComfyUI 0.42.1 (GPU media crash), llama.cpp b11800 (experimental acceleration). **Не ставить всё сразу.** Для каждого pinned upstream tag, license, install risk, reproduce test, comparative result/rollback; UNKNOWN или REJECTED — честные результаты.

## Checkpoint & GitHub discipline

Работай на проверяемой integration branch. Перед каждым изменением и после каждого green gate создай датированный `docs/owner/runs/north-star-20261023/CP-NNN-<topic>.md` и **коммить и push** (без секретов); не держать evidence только локально. Записывай code commit SHA отдельно от doc commit SHA. Source HEAD зафиксирован для тестов. Не коммитить временные bearer tokens, разговоры и PII. Не мержить самопроизвольно в `main`: owner approval + verified gates. При конфликте с активным Windows процессом не убивать его ради чистого рабочего дерева.

Обнови:
- `command-center/bcc/capability_tree_seed.json` **только** по нормальному flow `tools/tree_registry_sync.py` / `tools/tree_apply_evidence.py`.
- `docs/owner/runs/north-star-20261023/LEAF_PROOFS.json` — по каждому ID verdict + evidence.
- `docs/owner/runs/north-star-20261023/NORTH_STAR_LADDER.md` — честные статусы для 8 ступеней.
- `docs/owner/runs/north-star-20261023/FINAL_ACCEPTANCE_RU.md` — CI, Windows proof, uptime, blockers.
- Корневой `README.md`: отдельные **Argon execution result** и **Codex independent review** секции. Не переписывать Codex viewpoint как утверждение Argon.

## North Star Ladder (не менять до evidence)

1. SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT.
2. SELF_REPAIR_SINGLE_CYCLE_PASS.
3. SELF_REPAIR_3_CYCLE_PASS.
4. TRANSFER_MEASURED_GAIN.
5. 24H_SOAK_PASS.
6. 48H_SOAK_PASS.
7. WEEK_MODE_READY.
8. REVENUE_CAPABLE_PILOT.

Никаких статусов READY на основании кода или 16 mock-based tests. Точные фактические результаты.

## Утренний запуск — 23 октября

1. Read AGENTS + all checkpoints, echo 7 paths и их SHA/commit.
2. Fetch latest main, frozen candidate, required workflow status, active runtime SHA.
3. Generate full 912-id reconciliation (904 active, 8 retired at 22.10 snapshot; пересчитать на текущем HEAD).
4. Fix failing exact-SHA CI/permissions/STOP before doing new work.
5. Complete the safe self-generated git-diff skill benchmark end-to-end.
6. Continue highest-impact unclosed leaves until evidence is complete or specific blockers remain.
7. Commit/push checkpoint after each independently validated step; open draft PR with tests. **Never declare all 912 VERIFIED unless receipts actually support this.**

**Твоя задача — довести один Bossman до измеримой автономности, а не написать ещё одну пачку промтов. Начинай с проверки фактов и реального исполнения.**
