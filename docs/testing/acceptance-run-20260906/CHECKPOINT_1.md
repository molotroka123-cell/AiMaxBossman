# CHECKPOINT 1 — acceptance-20260906-01 (continuation run)

Дата: 2026-09-06 ~21:30 CEST. Прогон продолжен с утра; настройка/аудит не перезапускались.

## Идентификация

- RUN_ID=acceptance-20260906-01
- START_SHA=d1e851bb5e70480c198ffd424fb04ae769a7c24a (origin/claude/bossman-control-v03-43igbk на старте)
- TESTED_CODE_SHA=a4da0f8737bcb2642f7c35596e500c55285468af (ветка acceptance/total-local-20260906 = d1e851b + fixes)
- REPORT_COMMIT_SHA=этот коммит (см. git log)
- ACTUAL_RUNNING_VERSION=Bossman Core 0.3 (bossman-core @ a4da0f8), Command Center 0.x (bcc @ a4da0f8)
- OWNER_SESSION_MATCH=session 1, user asd\timur (интерактивная консоль; сервисы запущены процессами этой сессии)
- Журнал вне tracked дерева: C:\bossman-acceptance\20260906T1945-d1e851b\ (manifest, findings.jsonl, junit, логи)

## Железо и модели (факт, не предположение)

- Windows 11 Home 26200, i9-14900HX 24C/32T, RTX 4060 Laptop 8GB, RAM 15.6GB (НЕ 128GB), C: свободно ~354GB
- Python 3.13.1 (venv приёмки), Node 25.8.2, FFmpeg/ffprobe 8.1, Chrome/Edge установлены
- Ollama 0.33.3 (endpoint фактически 127.0.0.1:11435, gateway определил автоматически)
- Локальные модели: qwen2.5:7b, llama3.2:latest, qwen2.5-coder:14b. GLM 5.3 НЕ установлен. minimax-m2.7:cloud — ЗАПРЕЩЕНА (нет бюджета, cloud fallback для PRIVATE выключен)
- ACTUAL_MODEL_ID=qwen2.5:7b — подтверждено реальным вызовом alias bossman-fast через gateway (ответ модели получен, usage записан)

## Runtime topology приёмки (изолировано от рабочей копии владельца)

- Worktree: C:\Bossman-acceptance-20260906 (отдельный, чужие worktree не тронуты)
- Postgres: docker pgvector/pgvector:pg16, 127.0.0.1:5433 (bossman/bossman, схема Core применена при старте; алхимия расширения vector потребовалась — первый образ postgres:16-alpine не подошёл)
- Gateway: 127.0.0.1:8877, config/gateway.local-hardware.yaml, backend ollama, alias bossman-fast→qwen2.5:7b, health ok
- Core: 127.0.0.1:8700 (PID 24660), WORKSPACE_DIR изолирован в C:\bossman-acceptance\...\runtime\workspace
- Device token: dev_c40e663eb9acdae8 (scopes chat,approve,events,admin), токен НЕ публикуется
- CC Command Center: будет поднят на 8801 для UI-сценариев D/E

## Результаты регресса на TESTED_CODE_SHA

| Набор | passed | failed | skipped | примечание |
|---|---|---|---|---|
| root (tests/) | 839 | 1 | 8 | единственный failed — host-sensitive гейт framework-overhead (<40ms; на этом хосте 48–50ms; подтверждён на pristine d1e851b: 49.5ms; калиброван на Linux CI) |
| bossman-core (4 CI-группы security/gateway/stage8-14/rest) | 663 | 0 | 19 | BOSSMAN_RUN_REAL_SANDBOX=0 |
| command-center | 1928 | 39 | 35 | триаж в OPEN_FINDINGS.json: 1 реальный дефект продукта (кластер H), остальное env/тест-долг |

## Исправлено в a4da0f8 (каждое с регрессией)

1. AT-01: COMPLETE верифицируется как любое действие (manager.py + verifier.py; регресс tests/test_operator_at01_at03_regression.py, 5 тестов)
2. AT-03: обязательное свежее re-observe после approval-wait перед эффектом + повторный policy-чек (manager.py; регресс 2 теста)
3. OP-PARK-001: коммит d1e851b ломал c5/c7 (owner pause/take_control в окне одобрения проглатывал stale-вердикт); parking сужен до recovery-парковки (waiting_approval_id is None)
4. CI-SKIPS-001: skips_registry рассинхронизирован (root-ci красный на HEAD); регенерирован (130→136 записей, --check PASS)
5. LINT-001: SyntaxWarning \W в test_tools.py:118
6. Hygiene root-тестов: POSIX-only тесты без платформенных гейтов (evidence_signing ×3, learning_trace flock, v5_observers symlink ×2, context_slice symlink, evening_harness bash+WSL-заглушка) — гейты добавлены, ожидания не ослаблены
7. tools/operator_step_profile.py: scripted COMPLETE приведён к новому контракту (postcondition "ok")

## Открытые находки (детали для интегратора — в OPEN_FINDINGS.json рядом)

- H-CLUSTER (реальный дефект Windows-пути): bcc/features/apps_control.py:254-263 _child_env не задаёт PYTHONIOENCODING/PYTHONUTF8 → дочерний процесс пишет лог в cp1252 → UnicodeEncodeError → ложный exit 1/'exited' вместо 3/'not_ready'. Воспроизведено автономно. Предлагаемый минимальный фикс: env["PYTHONIOENCODING"]="utf-8"; env["PYTHONUTF8"]="1" в _child_env.
- golden_missions 1/2/3/9/11/12: фикстуры используют sh-диалект (printf/heredoc); на Windows без sh.exe честно парковались в waiting_approval — конвейер approval→resume исправен (mission_10 deny проходит). Либо кросс-платформенные фикстуры, либо skipif which('sh') is None.
- finalize_unclassified + mission010: тесты кодифицируют поведение до харденинга песочницы (terminal_run без mode → ask) — тест-долг, кросс-платформенно.
- Host-sensitive: root operator-overhead гейт и v5_human_speed CAS p100<10ms флапают под нагрузкой Windows-хоста (задокументировано в CLAIMS_NOT_PROVEN.md).
- CC video read_verification кластер A (7 тестов): поведение identity/rehash на Windows (WinError 32 при unlink/replace под живым handle) — требует разбора отдельно от приёмочного прогона.

## Следующие шаги прогона

1. Сценарий A (Проводник+Блокнот) через computer operator Core (BOSSMAN_LOCAL_AGENT, qwen2.5:7b) с независимой проверкой артефакта.
2. Сценарии B/C (Калькулятор, браузер) тем же путём.
3. D/E: Video Studio и Web Designer через реальный UI CC (Chrome) — TESTER_UI + agent-чат.
4. Скорость: UI feedback/stop-ack/CAS/cycle p50/p95 (≥100 повторов механики).
5. Пауза/стоп/рестарт оператора + контроль отсутствия повторного эффекта.
