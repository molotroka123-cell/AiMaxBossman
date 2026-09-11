# Матрица отказов внешних интеграций (раздел 22)

Раздел 22 перечисляет девятнадцать режимов отказа и требует одного:
**Bossman остаётся честным под отказом. Успех не изготавливается ради процента
прохождения.**

Таблица ниже — не план, а сверка: для каждого режима назван ТЕСТ, который
покраснеет, если поведение вернётся к нечестному. Там, где теста не нашлось,
это написано прямо, и либо тест добавлен в этом прогоне, либо названа причина,
по которой его нет.

Сокращения путей: `cc/` = `command-center/tests/`, `root/` = `tests/`,
`core/` = `bossman-core/tests/`.

| # | Режим отказа | Что обязано произойти | Чем проверено |
|---:|---|---|---|
| 1 | бэкенд отсутствует | откат на проверенную реализацию; если её нет — отказ, а не «ок» | `cc/test_hybrid_registry.py::test_registry_fallback_on_unregistered`, `::test_registry_fails_closed_when_legacy_is_unhealthy` |
| 2 | **неверная версия** | бэкенд не используется: запущено не то, что разбиралось по лицензии | `cc/test_hybrid_registry.py::test_a_backend_that_drifted_off_its_pin_falls_back_to_legacy` и ещё пять — **добавлено в этом прогоне, BL-028** |
| 3 | зависимость отсутствует | сайдкар не поднимается, ошибка названа | `cc/test_hybrid_sidecar.py::test_sidecar_missing_binary` |
| 4 | падение процесса | обнаружено, перезапуск ограничен, сирот не остаётся | `cc/test_hybrid_sidecar.py::test_sidecar_lifecycle_normal`, `cc/test_startup_failure_cleanup.py` |
| 5 | искажённый протокол | ответ отвергнут ДО создания улики | `cc/test_hybrid_windows_mcp_strict_contract.py::test_click_rejects_provider_error_status_before_evidence_creation` |
| 6 | частичный ответ | «строка `false`» не превращается в `True` | `cc/test_hybrid_windows_mcp_strict_contract.py::test_close_rejects_truthy_string_false_instead_of_converting_to_true` |
| 7 | таймаут | `OperationTimeoutError`, задача не завершена | `cc/test_hybrid_windows_mcp_spike.py`, `cc/test_hybrid_context_store.py` |
| 8 | отмена | корреляция эффекта отменяется, эффект не идёт | `cc/test_hybrid_capabilities.py::test_effect_correlation_cancellation` |
| 9 | владелец остановил | то же, что отмена, плюс перехват управления | `cc/test_owner_control_ui.py`, `core/test_stage13_operator_redteam.py::test_emergency_lock_revokes_lease_and_locks_tasks` |
| 10 | полномочие отозвано ПО ХОДУ | эффект запрещён на границе эффекта, а не «уже начали» | `root/test_v5_admission.py::test_revoke_while_queued_fails_effect_boundary_reauthorization`, `core/test_fleet_remote_rpc.py::test_revoked_between_admission_and_effect` |
| 11 | устаревшее событие | просроченная корреляция не принимается | `cc/test_hybrid_capabilities.py::test_effect_correlation_expiration` |
| 12 | повторное событие | одна и та же заявка даёт один допущенный интент | `root/test_v5_admission.py::test_duplicate_proposal_identity_yields_one_admitted_intent` |
| 13 | не та цель | отпечаток цели снимается ДО эффекта и сверяется | `cc/test_hybrid_windows_mcp_spike.py`, `cc/test_hybrid_evidence.py` |
| 14 | нехватка ресурсов | отказ, а не OOM; неизвестные данные — не ёмкость | `cc/test_v6_resources_unmeasured.py` (включая **BL-024** этого прогона), `core/test_resource_brain.py::test_admit_allows_within_budget_denies_over_budget` |
| 15 | модель недоступна | откат по здоровью; нездоровый бэкенд не выдаётся | `cc/test_hybrid_registry.py::test_registry_falls_back_when_selected_optional_backend_is_unhealthy` |
| 16 | сети нет | отказ назван; выдумывать ответ нельзя | `cc/test_web_research_net.py`, `cc/test_openrouter_connect_ui.py` |
| 17 | перезапуск ПО ХОДУ | состояние восстанавливается, эффект не удваивается | `root/test_v5_recovery.py::test_crash_after_external_effect_before_journal_commits_once`, `::test_crash_after_journal_before_receipt_no_duplicate_on_second_recover` |
| 18 | **успех без эффекта** | НЕ завершено: наблюдение ≠ улика | `cc/test_hybrid_evidence.py::test_external_success_without_bossman_verification_fails`, `root/test_v5_recovery.py::test_tool_success_without_post_state_observation_is_unknown` |
| 19 | эффект при ответе с ошибкой | неоднозначность объявляется явно, а не решается в чью-то пользу | `root/test_v5_recovery.py::test_crash_during_verification_yields_unknown_not_satisfied`, `::test_irreversible_unknown_parks_rather_than_replays` |

## Что было НЕ покрыто и стало покрыто

**Режим 2, «неверная версия».** `BackendVersionMismatchError` был объявлен в
`capabilities.py`, экспортирован из `hybrid/__init__.py` — и **не возбуждался
нигде**. Поиск по всему дереву давал три совпадения: объявление, экспорт и
импорт.

Это не мелочь именно из-за раздела 18. Весь смысл `docs/hybrid/sources.lock.json`
— записать ТОЧНЫЙ SHA каждого внешнего проекта, по которому проверялась
лицензия, телеметрия и поведение. Пока рантайм ни разу не сверяет, что запущено
именно это, закрепление остаётся утверждением в документе: подменить бинарь
сайдкара было бы некому заметить.

Добавлено в `AdapterRegistry`:

* `pin_version(capability, backend, version)` и необязательный
  `pinned_version=` у каждой `register_*`;
* сверка при разрешении бэкенда — ПОСЛЕ проверки здоровья (больной процесс не
  обязан уметь называть версию, и сообщать про «версию» там, где мёртв процесс,
  значит увести читателя не туда);
* «версия неизвестна» считается несовпадением, а не совпадением;
* несовпадение у необязательного бэкенда → откат на проверенную реализацию;
  несовпадение у самой проверенной реализации → `BackendVersionMismatchError`,
  потому что отступать больше некуда;
* обратный контроль: НЕзакреплённый бэкенд работает как раньше — иначе
  «починка» вида «требовать пин всегда» сломала бы всё незакреплённое.

Проверено мутацией: если заставить сверку версий всегда отвечать «совпадает»,
краснеют четыре из шести новых тестов.

## Оговорка, без которой таблицу читать нельзя

Ни один внешний бэкенд сегодня НЕ ЗАРЕГИСТРИРОВАН: `registry = AdapterRegistry()`
пуст, и раздел 4 задания прямо запрещает делать внешний движок умолчанием до
зелёной базовой линии. Поэтому строки таблицы проверяют КОНТРАКТ отказа на
двойниках адаптеров, а не поведение конкретного стороннего процесса под нагрузкой.

Это ограничение, а не оправдание: контракт — то единственное, что можно
проверить до того, как владелец включит бэкенд, и он написан так, чтобы первое
включение не потребовало доверия к чужому коду. Настоящий прогон против живого
Windows-MCP остаётся владельческим и назван в `TARGET_HARDWARE_REPORT.md`.
