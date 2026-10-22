# Завтра: запуск роя самоулучшения и прогон Tier 5 — runbook (22.10.2026)

Статус: **план и чек-лист, не сертификат**. Всё, что ниже названо PROVEN, имеет путь к улике в дереве; остальное — нет. Правила не ослабляются: [`AGENTS.md`](../../AGENTS.md), [`docs/evo/BOSSMAN_1_1_NORTH_STAR.md`](../evo/BOSSMAN_1_1_NORTH_STAR.md), аудит «уток» [`CLAIMS_AUDIT_20261022_RU.md`](CLAIMS_AUDIT_20261022_RU.md), уборка [`CLEANUP_INVENTORY_20261022_RU.md`](CLEANUP_INVENTORY_20261022_RU.md).

## 1. Честная позиция на лестнице North Star

| Ступень | Статус | Улика / почему |
|---|---|---|
| SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT | **есть** | код и тесты: `tools/bossman_15_self_improve.py`, `tools/self_improve_lab.py`, `tools/tree_self_improve.py`, `command-center/bcc/self_improve`; AGENTS.md |
| SELF_REPAIR_SINGLE_CYCLE_PASS | **не доказан на текущем SHA** | исторические циклы — `docs/evidence/north-star-20261010/`, `docs/architecture/bossman-tree-20261005/evidence/selfrepair-*`; перепроверить на живом SHA |
| SELF_REPAIR_3_CYCLE_PASS | **не доказан** | `docs/audits/2026-10-18-swarm-self-improve`: run1 NOT_RUN |
| TRANSFER_MEASURED_GAIN | **не доказан** | `docs/audits/2026-10-19-final/g4-transfer`; в receipt `transfer_30_tasks_status: NOT_RUN`; «+40%» — coaching, см. аудит строки 2-3 |
| 24H / 48H_SOAK_PASS | **NOT_RUN** | `e2e_stop_holdout_receipt.json` → `soak_24h_48h_single_sha: NOT_RUN` |
| WEEK_MODE_READY, REVENUE_CAPABLE_PILOT | **нет** | `audited_external_revenue_documents: BLOCKED` |

Tier 5 **не доказан** (`tier5_status: NOT_PROVEN`). Отчёт 2.6 `READY` относится к его этапам, не к Tier 5.

## 2. Предусловия (не стартовать, пока любой пункт не «да»)

- [ ] `git fetch`; `origin/main`, установленный build, runtime `:8801` — один SHA; `GET /health/live` → `source_identity=PASS`; SHA записан в отчёт.
- [ ] Обязательный CI зелёный на этом же SHA (не локальные JUnit).
- [ ] Jeff на паузе (`hold-jeff`), пока владелец не снял: `BossmanOne-6-Jeff` не включать.
- [ ] Генерации владельца — приоритет; resource governor включён; роем не занимать GPU во время фото/видео.
- [ ] STOP проверен: файл/эндпоинт `POST /api/autonomy/stop`, `STOP_ORCHESTRA`, `tools/owner_one_bossman.py stop ... --hold` (формат — `BOSSMAN_ONE_STATE_20261018_RU.md`).
- [ ] Бюджеты: `autonomy/long_run.json` (DeepSeek 2, GLM 2, Haiku 2, Mistral 0 $/сутки); RunPod **запрещён**; платное сверх лимита — только с «да» владельца.
- [ ] Рой не вливает в `main`; только ветка/кандидат → тесты → независимая проверка → кнопка владельца.
- [ ] Конституция v2 и пин не трогать; ключи и vault не коммитить; `python tools/ci_secret_scan.py` перед каждым push.

## 3. Точки входа, которые есть в дереве (флаги смотреть в `--help`, здесь не выдумываются)

| Назначение | Файл |
|---|---|
| Проверка Tier 5 по классам улик (SYNTHETIC / HISTORICAL_LIVE / CURRENT_LIVE) | `tools/verify_tier5_argon_e2e.py` |
| Самоулучшение (15 направлений) | `tools/bossman_15_self_improve.py`, `tools/self_improve_lab.py`, `tools/tree_self_improve.py` |
| Оркестр бесплатных моделей | `tools/limits_out_orchestra.py` |
| Память/обучение (держит holdout) | `tools/prove_stage6_memory_and_learning.py` |
| Приёмка владельца (этап 7) | `tools/prove_stage7_owner_acceptance.py` |
| Self-coding через OpenCode Web Pult | `tools/prove_opencode_web_pult_self_coding.py` |
| Точная сертификация SHA | `tools/exact_sha_certify.py` |
| Гигиена | `tools/ci_secret_scan.py`, `tools/skips_registry.py --check`, `tools/docs_link_check.py` |

## 4. Ворота Tier 5 и чем проверяются

- G0 SHA/identity — `/health/live` + сверка seed/registry/bundle.
- G1 сквозной сценарий владельца — **сейчас SYNTHETIC**; нужна одна реальная миссия через пульт/телефон с task ID, STOP, независимым постусловием и квитанцией.
- G2 закрытый holdout — **сейчас SYNTHETIC**; нужен заранее зафиксированный 15×3 blind holdout (пороги до замера).
- G3 STOP — CURRENT_LIVE есть в receipt (`stop_http_latency_ms 735.28`); повторить на завтрашнем SHA.
- G4 перенос без памяти (веса/модель) — `NOT_RUN`.
- G5 soak 24/48 ч на одном SHA — `NOT_RUN`.
- G6 выручка по внешним документам — `BLOCKED`.
- Остальное — по `docs/audits/2026-10-19-final/README.md`.

## 5. Стоп-условия и откат

Остановить рой и зафиксировать причину, если: смена SHA в ходе прогона; расход выше лимита; появился доступ к holdout/ключам; любое неподтверждённое действие вовне; действие после STOP. Откат: рой работает только в кандидатных worktree/ветках — удалить ветку; установленный stable не переписывается; `git revert` для влитого.

## 6. Что рою можно и нельзя

Можно: читать код, писать патчи в изолированном worktree, гонять целевые тесты, писать улики в `docs/audits/<дата>`. Нельзя: пушить в `main`, трогать `wt-checkpoint-20261015` (живой `:8801`), конституцию и пин, vault/ключи, личные чаты/фото, включать Jeff, арендовать RunPod, засчитывать memory hit или патч учителя как обучение.

## 7. Кто что делает

Argon — интегратор и единственный, кто меняет общий код; Claude — аудит, документация, независимая проверка; владелец — кнопка слияния в `main`, снятие паузы Jeff, платные расходы, внешние действия.

## 8. Отчётность

Пульт, по-русски, **жирным** — вердикт и SHA, `кодом` — пути и команды; PASS/FAIL/NOT_RUN/BLOCKED с путём к улике; ежедневно и при каждой ступени лестницы.

## 9. Что НЕ доказано / риски

- Tier 5, перенос, soak, выручка — см. раздел 1.
- 6 красных проверок CI на `f6467b7d` (причины тогда не установлены); перепроверить.
- `docs/testing/sessions/*.jsonl` в git могут содержать персональные данные — не публиковать без проверки.
- Результаты Argon из `test/tools-skills-proof-20261021`: ⟦ЗАПОЛНИТЬ ПРИ СБОРКЕ: результаты прогона тестов Argon из docs/audits/2026-10-22-tools-skills-proof⟧
- Живой SHA/CI на момент старта: ⟦ЗАПОЛНИТЬ ПРИ СБОРКЕ: runtime SHA, origin/main SHA, статус обязательного CI⟧
