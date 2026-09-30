# Исторические инварианты безопасности и их тесты (раздел 20)

Раздел 20 задания называется «DO NOT DELETE HISTORICAL SAFETY FIXES» и
перечисляет двенадцать инвариантов, которые достались дорого. Требование к
этому прогону — «Add regression tests where absent».

Документ отвечает на один вопрос для каждого инварианта: **какой тест
покраснеет, если инвариант вернётся в нарушенное состояние.** Инвариант без
такого теста — это не инвариант, а обещание.

Сокращения: `cc/` = `command-center/tests/`, `root/` = `tests/`,
`core/` = `bossman-core/tests/`.

| # | Инвариант | Тест-сторож |
|---:|---|---|
| 1 | текст не может удовлетворить эффект в реальном мире | `core/test_at01_completion_obligations.py::test_p0_3_a_mere_input_act_never_absorbs_a_stronger_promise` |
| 2 | нельзя слепо доверять устаревшим флагам журнала завершения | `root/test_v5_recovery.py::test_crash_after_journal_before_receipt_no_duplicate_on_second_recover` |
| 3 | личности плана/задачи/улики при возобновлении обязаны совпадать с текущей миссией | `root/test_v5_satisfied_evidence_gate.py::test_evidence_bound_to_a_different_objective_is_refused`, `::test_evidence_bound_to_an_older_revision_is_refused` |
| 4 | улику нельзя переиграть против другого ожидаемого значения или другой миссии | `root/test_v5_satisfied_evidence_gate.py::test_stale_evidence_is_refused`, `::test_a_tampered_binding_field_is_refused` |
| 5 | падение между эффектом и фиксацией журнала обязано стать ЯВНОЙ неоднозначностью | `root/test_v5_recovery.py::test_crash_after_dispatch_before_external_effect_parks_irreversible`, `::test_crash_during_verification_yields_unknown_not_satisfied` |
| 6 | аренда + владелец + ограждение проверяются в нужный момент, включая завершение | `core/test_v3_fence_receipts.py::test_zombie_receipt_under_stale_fence_is_not_evidence`, `core/test_v3_fleet_core.py::test_lease_ttl_renew_expire_and_fencing` |
| 7 | задача, ждущая человека, не становится сразу перехватываемой другим исполнителем | `core/audit001/test_f2_abandon_race.py::test_a_crash_between_claim_and_complete_does_not_auto_release_the_claim` |
| 8 | проверки DNS/домена/переадресации/соединения остаются безопасными | `cc/test_secrem_browser_policy.py::test_repro_default_policy_denies_private_and_metadata_targets`, `::test_variant_hostname_resolving_to_private_is_refused` |
| 9 | полномочие отозвано до эффекта → эффект запрещён | `root/test_v5_admission.py::test_revoke_while_queued_fails_effect_boundary_reauthorization`, `core/test_fleet_remote_rpc.py::test_revoked_between_admission_and_effect` |
| 10 | смена класса ошибки не создаёт бесконечный бюджет повторов | `cc/test_v7_recovery_bounded.py::test_rotating_n_classes_terminates_within_the_budget`, `::test_alternating_two_classes_terminates_with_the_degraded_path_spent_once` |
| 11 | неизвестные, устаревшие, NaN, отрицательные и противоречивые данные о памяти — НЕ безопасная ёмкость | `root/test_v5_admission.py::test_unknown_cost_is_never_reserved_as_zero`, `cc/test_v6_resources_unmeasured.py::test_an_override_without_a_measurement_is_not_capacity` (**добавлено в этом прогоне**) |
| 12 | «не используй инструменты» не превращается в требование применить инструмент | `cc/test_action_contract_negation.py::test_a_prohibited_action_is_not_a_required_action`, `::test_negation_removal_is_span_local_not_sentence_wide` |

## Единственный пробел, который сверка нашла, и он был зелёным

Одиннадцать инвариантов из двенадцати уже имели сторожа. Двенадцатый —
**номер 11, ресурсный допуск** — имел тест только на один из двух путей.

Существующий `test_unmeasured_memory_is_null_in_the_api_and_refuses_admission`
проверял случай, когда замера нет И числа нулевые. Но нули приходят не всегда:
`_snapshot` подставляет ручной `total_override_mb` (поле есть на странице
«Ресурсы») ДО того, как узнаёт, был ли замер. Снимок при этом честно говорит
`measured=False` — а `plan_memory` этот флаг вообще не читал и выдавал
«112000MB свободного бюджета».

Разбор — `BUG_LEDGER.md`, BL-024. Проверка перенесена в САМО решение, потому
что путей в него два (прямой допуск и допуск через выгрузку простаивающих), и
правка у одного из вызывающих закрыла бы только первый.

**Инвариант, который стоит унести дальше:** флаг «данные недостоверны» обязан
проверяться там, где принимается решение. Флаг, на который полагаются косвенно
— через форму чисел, — перестаёт работать в первый же день, когда числа
приходят другим путём.

## Как этим пользоваться следующей модели

Если вы собираетесь удалить или упростить любой тест из таблицы: это не
«лишняя проверка». Каждая строка стоила отдельного дефекта, и почти каждая
выглядит избыточной РОВНО ДО ТОГО, как её снимут. Прежде чем трогать, найдите
разбор в `BUG_LEDGER.md` и прочитайте, что именно сломалось.
