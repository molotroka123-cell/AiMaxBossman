# verifier-pre (независимый проверяющий, base 6869f67a, кандидат 803aa4d9)

## A. 803aa4d9 (BOSSMAN_VERIFY_PYTHON облачному воркеру) — PASS с замечанием
- Новый тест на старом coding_tasks.py: КРАСНЫЙ (1 failed/3 passed; env = {'BOSSMAN_WORKER_API_KEY': 'k-123'}, без BOSSMAN_VERIFY_PYTHON). На новом коде: ЗЕЛЁНЫЙ.
- Тест не слабый: вызывает реальный `_run` (облачная ветка body.worker), перехватывает env, переданный в `_execute`, проверяет ключ, интерпретатор и отсутствие посторонних секретов.
- Регрессии (10 файлов coding/sidecar: apply, path_owner_tool, recipes, tasks, cloud_readiness, local_sidecar, workers, hybrid_sidecar, sidecar_verify_python_env, ux_soak_coding_page_readiness): база 86 passed/0 failed; новый код (BOSSMAN_VERIFY_PYTHON не задан) всё зелёное; полный `command-center/tests` не гонял.
- СЛАБОСТЬ (реальная): существующий test_coding_workers.py::test_a_cloud_worker_task_gets_only_its_own_key_and_command сравнивает env строго `== {"BOSSMAN_WORKER_API_KEY": ...}`. Если BOSSMAN_VERIFY_PYTHON задан в окружении (на машине владельца он задан), на новом коде тест КРАСНЫЙ (проверено: SET -> 1 failed, UNSET -> 5 passed; на базе SET -> зелёный). Коммит не обновил этот тест и не изолирует env -> нужен monkeypatch.delenv либо `<=`/проверка подмножества.
- Мелочь: новый тест использует `next(iter(WORKERS))` (любой воркер) и `__import__("pathlib")`; ок, но не проверяет локальную ветку заодно (она покрыта старыми тестами).

## B. Holdout'ы — PASS
- Запуск обычным python (без pytest) с `--repo <checkout> [--out]`; exit 1 при провале, 2 при загрузке.
- База 6869f67a: discovery 37/72 passed (35 fail, как ожидалось); goal_budget 4/9 passed (5 fail).
- Лежат в tools/tree_holdout/, вне allowed воркера (goal-budget: allowed = goals.py + test_autonomy_goals.py; discovery: жёсткий ОБЪЁМ в wish); добавлены одним коммитом ee21795c, правок/ослаблений в истории нет (0 удалённых строк).
- Замечание: защита путей держится на wish-тексте и `allowed`; для режима tree явного запрета на tools/tree_holdout в коде я не проверял — рекомендую, чтобы swarm_verify_patch (или цикл) отвергал патчи в tools/tree_holdout/ (мой скрипт отвергает).
- Замечание: сам holdout в --out пишет JSON только при указании пути (stdout только сводка).

## C. Чеклист «полный цикл» и tools/swarm_verify_patch.py
Полный цикл = (1) красный тест/holdout на базе; (2) воркер сам пишет тест, падающий на старом коде; (3) патч только в allowed, без tools/tree_holdout; (4) holdout на патче = 100%; (5) zone pytest зелёный (в т.ч. при заданном и незаданном BOSSMAN_VERIFY_PYTHON); (6) независимая перепроверка на чистой копии (этот скрипт) = PASS; (7) рецепт сохранён только после PASS; (8) evidence + аудит владельцу.
Команда: `python tools/swarm_verify_patch.py --repo <checkout> --patch worker.diff --holdout goal-budget|discovery --base <rev> --zone-test <test.py> [--allowed a b]` (exit 0 PASS / 1 PARTIAL / 2 FAIL; stdlib, `git archive` во временную папку, checkout не трогает).
Проверено: пустой патч -> FAIL (rc=2); патч в tools/tree_holdout -> FAIL; no-op патч (комментарий в goals.py) -> FAIL (holdout 4/9 -> 4/9, zone 34 passed); положительный PASS-путь на реальном фиксе не проверялся (фикса нет).
Ограничение: PARTIAL = holdout улучшился, но не 100% или zone красный; zone-тесты берутся из патча, если --zone-test не задан.
