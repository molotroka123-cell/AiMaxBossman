# Swarm 07.10.2026 — handoff (одна ветка swarm/selfrepair-20261007)

Ступень North Star: SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT. SELF_REPAIR_SINGLE_CYCLE_PASS **не достигнут**; полный цикл = PARTIAL. Terminal Run остаётся поверхностью того же Bossman (тот же backend :8801, та же память).

## Кто что сделал
| Автор | Что |
|---|---|
| Claude (инфраструктура, НЕ самоисправление Bossman) | 803aa4d9: облачный воркер получает BOSSMAN_VERIFY_PYTHON; a0081ac3: изоляция строгого теста env + tools/swarm_verify_patch.py (проверяющий) |
| Исследователь (агент, read-only) | queue.md/json: 8 задач, расхождения сайт/реестр = 0 по статусам; 11 reported-листьев без PASS-receipt |
| Проверяющий (агент) | verifier-pre.md: тест фикса красный на старом коде, зелёный на новом; holdout discovery 35/72 fail, goal-budget 5/9 fail; слабость теста найдена и исправлена в a0081ac3 |
| Bossman (бесплатный Nemotron 3 Super, openrouter-free, $0) | cycle14: правки discovery.py + тесты, holdout 35 → 15 падений (все None), остановлен сторожем песочницы; cycle15: только тест, 35 → 35 |

## Результат
- DEFECT_REPRODUCED: да (2/2). MODEL_PATCH_CREATED: нет (патч не принят: статус failed). INDEPENDENT_VERIFICATION_PASS: нет. EXPERIENCE_AUTO_SAVED: нет. Перенос на goal-budget: NOT_RUN.
- Switch на сборку 803aa4d9 выполнен (rollback: bugtest-20261001/tree-1005/switch-803aa4d9/rollback.ps1); pytest у облачного воркера теперь работает, в cycle14 воркер впервые дошёл до правки кода.
- Память-рецепт воркером вспоминалась (recalled=true), но результат не менялся: см. память qwen-lessons-do-not-change-outcomes.
- Расходы: платных вызовов 0. Токены/длительность по воркеру: cycle14 309 с, cycle15 см. cycle15-summary.json; токены UNKNOWN (API не вернул).
- OWNER_HW: NOT_RUN (просмотр владельцем).

## Точный следующий шаг
1. Добавить `nvidia-nim` (бесплатный NIM, правило владельца) в FREE_WORKERS tools/tree_self_improve.py и прогнать cycle16 с ним; либо поднять порог сторожа «8 повторных наблюдений» для tree-кейсов (правка sidecar — Claude-инфраструктура, помечать отдельно).
2. После PASS на discovery — cycle на goal-budget с --transfer (проверка рецепта после перезапуска).
3. Не считать Claude-патчи как самоисправление.
