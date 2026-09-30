# SANDBOX_REHEARSAL: ограниченный цикл самоулучшения Bossman (30.09.2026)

Метка честности: **SANDBOX_REHEARSAL, пин тестовый, не владельца.** Пин конституции владельца не тронут. Цикл выполнен в песочнице
(`swarm-20260930\autonomy\sandbox`, сгенерированный репозиторий, отдельный root и тестовая копия конституции). Цикл на корне владельца (JEFF-0042)
не запускался: он ждёт `bossman autonomy constitution pin` в интерактивном терминале владельца.

Что здесь показано (`summary.json`, `journal.jsonl`, `manifest.json`, `trace.json`):
- планировщик: бесплатная Nemotron-3-Ultra 550B (NVIDIA NIM, цена 0/0 проверена по каталогу) выбрала цель из двух;
- писатель: настоящий `claude -p` (haiku) создал `tests/test_clamp.py`; кандидат sha + diff-хэш зафиксированы;
- тесты Bossman (acceptance и protected) через `HandBroker`; ревью Claude APPROVE и Codex APPROVE по одному sha + diff + улике;
- staging на отдельном порту, оценка метрик ДО владельца (ACCEPT), стоп на `USER_APPROVAL`;
- журнал hash-chain verify=OK (45 записей), манифест полный; опыт записан как UNVERIFIED-урок (`WEIGHTS_UNCHANGED`, `retrieval_context`);
- `stopdrill-stop-drill.json`: `bossman autonomy stop` убил живой `claude.exe` за 0.4 с;
- `first-run-blocked-max-turns-summary.json`: первый прогон упал честно (`error_max_turns` при 3 ходах Claude), для живого цикла нужен `--max-cli-turns 8`.

Чего здесь НЕТ: цикла на корне владельца, автоприменения (выключено по замыслу), автооткатa, измеренной пользы, 24/7 soak.
North Star: `SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT` (не повышен).
