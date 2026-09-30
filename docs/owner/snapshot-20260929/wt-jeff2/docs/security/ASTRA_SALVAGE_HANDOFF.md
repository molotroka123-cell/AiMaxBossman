# Повторный аудит и передача Fable

MISSION_ID: BOSS-ASTRA-AUDIT-SALVAGE-004
START_SHA: 35390ec0c85df3f99adb16bea8dcfaf4f5ccef4c
REMOTE_OBSERVED_SHA: 87bf7c75cc96f8b0b7b3aeae6fc55cfeb3bd44d3
Исходный forensic аудит остаётся закреплён за 7b1377a. Его 5050/10000 — историческая
оценка, не новая аттестация. Новый числовой балл не назначался.

Перепроверены 35 находок. Между 7b1377a и начальным 35390ec файлы, указанные как
основные доказательства этих находок, не изменились. Позднее ветка продвинулась до
87bf7c7: получены ActionReceipt, канонический BCC finalizer, новые post-state verifiers,
trace/retention, Fleet summary, запрет перезаписи закрытого journal и safe_task_id.
Они сохранены при объединении. Таблица DELTA_AUDIT.csv различает начальную базу,
частичные изменения удалённой ветки и результат локальных исправлений.

Дополнительно исправлены:
- Конкурирующие процессы и устаревшие экземпляры TaskJournal: exclusive writer lock
  и compare-and-swap. Сохраняются durable intent, подпись и запрет перезаписи done.
- Ошибка независимого чтения результата переводит Core в unverified; само чтение
  ограничено по объёму, включая рост файла после проверки размера.
- CGNAT/непубличные IP запрещены; DNS вынесен из event loop, поздний ответ после
  тайм-аута не может выдать разрешение живому HTTP транспорту.
- SCA отвергает пропущенные сторонние зависимости и пустой отчёт.
- При объединении ActionReceipt сохраняет observation/expectation/attempt bindings
  Astra и fencing_token новой ветки; fleet_dispatch исключён из неизменного digest
  контракта как служебный конверт. Проверка fence до эффекта сохранена.
- Широкие исключения scanner по словам fake/synthetic и форме base58 не приняты:
  используются точечные пометки известных публичных/синтетических значений.
- Установщик поддерживает базу 35390ec, исходники наблюдённой 87bf7c7 и предыдущий
  пакет. Конфликтующие пользовательские изменения не перезаписывает.

31 исходная находка по коду исправлена в пределах указанных локальных сценариев.
4 CI-находки имеют подготовленные исправления, но внешняя приёмка остаётся открытой.
Это не утверждение о полном отсутствии уязвимостей или готовности к автономной работе.
Remote transport не реализован; live Solana не включён.

Публикация: обычный git push не получил credentials. Подключённый GitHub вернул
403 Resource not accessible by integration при create_tree. Удалённая ветка этим
исполнителем не изменена. Branch API для 87bf7c7 показал protected=false, rulesets=[].
Exact-SHA CI локального результата: NOT_RUN. Windows ACL/реальное железо/sandbox:
NOT_RUN. Платных вызовов OpenRouter в этой работе не было.

Проверки до объединения: Core 248 passed; полный CC 1388 passed, 66 skipped;
root 146 passed. После объединения выполнены отдельные регрессии, результаты в
validation/REAUDIT_RESULTS.json. Полный CC после объединения оставлен Fable;
предыдущий полный прогон не переносится на новую версию автоматически.

READY_FOR_REAL_OPENROUTER_AGENT_TEST=BLOCKED_PENDING_FINAL_SHA_CI
READY_FOR_AI_MAX_ACCEPTANCE=NO_HARDWARE_ATTESTATION
READY_FOR_AUTONOMOUS_OPERATIONS=NO
NEXT_HIGHEST_VALUE_FIX=Final-SHA broad regression, Windows ACL/portable acceptance,
exact-SHA CI and reconciliation of unknown external effects on target environment.

После объединения: Core 254 passed; CC 42 passed, 2 failed в новых process-verifier tests
(test_process_verifier_uses_live_pid, test_verify_all_aggregates_new_kinds_fail_closed).
Фактическое наблюдение: проверка текущего PID возвращает running=false в этом контейнере;
причину и целевую Windows/Linux совместимость должен проверить Fable. Нельзя считать этот
прогон полностью зелёным. Root scanner/gate/ActionReceipt: 15 passed.
