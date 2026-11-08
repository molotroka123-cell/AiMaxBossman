# Dataset Card: Bossman Source-Grounded Computer-Use & Safe Growth LoRA Corpus (`2026-11-08`)

## 1. Назначение и архитектура (RAG vs. LoRA Weights)
Этот корпус собран исключительно из проверенных файлов репозитория `AiMaxBossman` с проверкой `SHA-256` каждого источника:
- **Что включено в LoRA-корпус (устойчивые навыки и политика безопасности)**:
  - схемы инструментов (`tool_schema_grounding`),
  - проверенные `SKILL.md` (`verified_skill_execution`),
  - `STOP-latch` и матрица разрешений (`stop_latch_and_permissions`),
  - проверка `runtime source identity` и git worktree (`runtime_source_identity`),
  - `checkpoint/resume` и охрана SQLite WAL (`checkpoint_and_resume`),
  - декомпозиция задач и маршрутизация локальных моделей (`task_planning_and_decomposition`),
  - `browser/computer-use` с обязательным проверяемым `expected_postcondition` (`browser_computer_use_postcondition`),
  - администрирование Telegram-контента с явной меткой `[BOT]` (`telegram_content_admin_workflow`),
  - UTM-аналитика и атрибуция на один профиль владельца (`campaign_analytics_and_attribution`),
  - защитная кибербезопасность (`defensive_cybersecurity`),
  - обработка ошибок и самовосстановление (`error_handling_and_self_repair`),
  - жёсткие отказы от финансово опасных, Sybil, wash-trading и спам-действий (`safety_refusal_financial_and_spam`).
- **Что НЕ включено в веса (остаётся в RAG)**:
  - текущие рыночные цены, балансы кошельков, живые счётчики подписчиков, внешние URL и любые изменяемые факты;
  - сырые чаты, личные фотографии, телефоны, сессии Telegram, cookies и приватные ключи.

## 2. Состав сплитов и изоляция Holdout
- `train.jsonl`: **40** записей (`80.0%` позитивного корпуса)
- `holdout.jsonl`: **10** записей (`20.0%` позитивного корпуса — строго `>= 15%`, пересечение с `train.jsonl` = `0`)
- `negative_controls.jsonl`: **10** записей (`100%` обязательный отказ от опасных команд)
- Всего записей: **60**

## 3. Результаты 5 защитных гейтов (Security & Provenance Gates)
- **Deduplication & Holdout Isolation Gate**: `PASS`
- **Secret Scan Gate**: `PASS` (`0` секретов/ключей)
- **PII Scan Gate**: `PASS` (`0` персональных данных/сырых чатов)
- **Prompt-Injection Scan Gate**: `PASS` (`0` неизолированных инъекций)
- **License & Provenance Gate**: `PASS` (`100%` файлов имеют подтверждённый `source_sha256`)

## 4. Статус обучения
- **Веса в этом прогоне НЕ обучались** (`weights_trained_in_this_run = false`).
- Resumable-конфигурация сохранена в `lora_training_config.json` для локального запуска на `AMD Ryzen AI Max+ 395 (128 GB)` только по отдельной команде владельца.
