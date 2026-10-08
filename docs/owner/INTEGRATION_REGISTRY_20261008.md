# Реестр сборки в одну линию, 08.10.2026

Линия: `integrate/bossman-2.1-one-20261006` (PR #99 в `main`). `main` = предок линии (отставания нет). Статусы листьев не менялись вручную.

## Ветки → линия

| Ветка | Не вошло в линию | Решение |
|---|---|---|
| green/tree-leaves-20261006, zone/apps, zone/memory, zone/plugins, zone/ux, swarm/selfrepair, swarm/direct-gen, swarm/direct-gen-multi, swarm/direct-gen-ui, merge-to-main-20261007, goal/bossman-self-improvement-tree-20261005 | 0 | Уже в линии. |
| PR #101–#105, #100 (K1m6a false-leak, resume, reasoning_effort, Mistral block replies, heretic encoder, план) | 0 после слияния | Влиты 08.10, PR закрыты как поглощённые. |
| audit/motion-concert-20261006 | 1 (документы) | Влито. |
| consolidate/jeff-20261007 (Jeff answering machine) | 7 | НЕ влито: вердикт аудита MERGE_WITH_FIXES, конфликт в `ui/tests/telegram_calls.test.mjs`, живой звонок не проверялся. Сохранено на origin. Блокер: исправления по аудиту + проверка на ПК. |

## Несохранённое локально (скопировано в `Bossman/handoff/preserve-20261008/`, не в git)

- `merge-to-main-20261007.bundle`, `swarm-direct-gen-multi.bundle` (ветки без удалённой копии; содержимое уже в линии).
- Патч `bossman-2.0-completion` (`tests/test_ux2_wizards.py`, 4 изменённых файла, HEAD отдельно): не в линии, решение владельца.

## Листья (832)

Полная таблица: `docs/architecture/tree-leaf-registry-20261008.csv` (лист, зона, источник, интеграция, доказано до, тесты, CI, ПК владельца, зависимости).

- Доказано до уровня: tests 485, code 280, none 67.
- Интеграция: code 510, recorded 180, branch 100, mixed 14, reported 11, blocked 10, idea 6, prepared 1.
- Уровни `ci` и `owner_pc`: NOT_RUN у всех 832. Дерево остаётся картой, а не сертификатом.
- Идеи (6) и blocked (10) не включаются ради зелёного дерева.

## Что блокирует «production в main»

1. CI на новом SHA линии: обязательная проверка `measured intelligence retention` требует комментарий владельца на точном SHA.
2. Слияние PR #99 в `main` только через зелёный CI, без force.
3. Установка точного SHA и прогон на ПК владельца (Windows-установка, Jeff, STOP, restart, GPU): NOT_RUN из облака.
4. K1m6a LESSONS/RESTART_TRANSFER: нужна установленная сборка с `/api/memory`.
5. vibeOS: после стабилизации основы.
