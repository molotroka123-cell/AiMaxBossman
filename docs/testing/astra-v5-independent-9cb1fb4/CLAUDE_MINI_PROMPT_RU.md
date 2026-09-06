# Мини-промпт: агент закрытия независимого V5-аудита

Opus, создай отдельного subagent `V5-Audit-Closure` для molotroka123-cell/AiMaxBossman. Ты остаёшься единственным интегратором; это рабочий агент Claude, не новый модуль Bossman.

Сначала fetch refs и актуальный PR37. Прочитай ветку `audit/astra-v5-independent-9cb1fb4-20260907`, каталог `docs/testing/astra-v5-independent-9cb1fb4/` и README. Исторический результат: SHA 9cb1fb4, 33 pass / 16 fail, ещё 6 AT-01 NOT_RUN; это не результат текущего HEAD.

Выдели агенту отдельные worktree/env/state и владение конкретными файлами. Перенеси тесты без ослабления. На новом фиксированном SHA воспроизведи и исправь IV5-CAN-001–003, IV5-PROM-001–003; proposals рассматривай как непроверенные предложения. CAN-002 закрывай через настоящий canonical evidence/activation caller, а не проверку формата ссылки. Выполни 6 AT-01 и actual-process recovery; неизвестный необратимый эффект остаётся на reconciliation без повтора.

Доведи существующие цепочки N4/N5/N6/N8: реальное обслуживание очереди, независимые измерения promotion/retention, workspace, canary/rollback. Не создавай параллельных ядер, не включай standing autonomy, не трогай live desktop/настоящую БД, не самоодобряй ASK. Изменения защищённых файлов — только после твоей явной передачи владения; интеграция последовательно через тебя.

Каждую находку закрывай только по исправлению и повторному отрицательному/положительному тесту. Сохрани SHA, import origins, команды, exit codes, JUnit/evidence; обнови существующие intake и M0–M11/N0–N8. Без снижения порогов, skip/xfail и выдуманного live PASS. Пуш только рабочей ветки, без merge/force-push в main. Финал: FIXED / STILL_OPEN / TESTED_SHA / TESTS / EVIDENCE / REMOTE_SHA / FREEZE_BLOCKERS. Незапущенное оставь NOT_RUN; V4/V5 закрытыми не объявляй.
