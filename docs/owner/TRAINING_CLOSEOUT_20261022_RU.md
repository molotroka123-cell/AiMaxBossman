# Закрытие обучений — инвентаризация треков (22.10.2026)

Статус: **черновик инвентаря, составлен в спешке (PC выключается)**. Трек считается закрытым только при наличии файла-улики в дереве. Обучение не запускалось. RunPod запрещён владельцем; на AMD нет CUDA, поэтому весовое обучение — только CPU/Vulkan-совместимое или отложено.

| Трек | Цель | Состояние | Улика в дереве | DONE / OPEN | Следующий один шаг | GPU/облако |
|---|---|---|---|---|---|---|
| LessonBook / исполняемые уроки | уроки, которые меняют исход | механизм есть; прирост не доказан (recall ≠ обучение) | `docs/owner/bossman_lessons_20261010.json`, `…20261011.json`, `docs/audits/2026-10-21-stage6-benchmark/stage6_live_holdout_proof.json` | DONE: хранение/рестарт; OPEN: перенос на закрытом holdout | прогнать заранее зафиксированный holdout без памяти | облако бесплатное (Groq/NIM), без RunPod |
| Jeff: методичка (KB) | Jeff отвечает строго по методичке | код и тесты есть; живой поллер на паузе | `command-center/bcc/features/jeff_kb.py`, `command-center/bcc/pit/`, `command-center/tests/test_jeff_manual_cloud_fallback.py` | DONE: код; OPEN: снятие паузы владельцем | решение владельца по `hold-jeff` | локально/бесплатное облако |
| Коучинг Qwen | подтягивать локальные Qwen | в контексте, веса не менялись | `docs/owner/AGENT_SCHOOL_REPORT_20261015_RU.md` (ветка agent-school) | OPEN: перенос без памяти `NOT_RUN` | G4 перенос на 30 задач | локально |
| LoRA / покер | модель для покера | RunPod запрещён 11.10 | `apps/poker-lora`, `docs/owner/runs/LORA_V3_REPORT_20261010.md` | OPEN | не запускать; ждать решения по железу | нужен CUDA — **нет** (AMD) |
| Дерево возможностей | листья с доказательствами | карта, не сертификат; 0 листьев `ci`/`owner_pc` на 08.10 | `docs/architecture/tree-registry.json`, `command-center/bcc/capability_tree_seed.json` | OPEN | пересинхронизация только через `tools/tree_registry_sync.py` | локально |
| K1m6a (трейдинг) | слепой перенос на экранах | документы и план | `docs/trading/K1M6A_RESUME_AND_PROVE_LEARNING_20261008.md` | OPEN | независимый аудит 10 видео | локально |
| Память / vault | заметки между сессиями | 38 заметок через рестарт (JSON) | `stage6_live_holdout_proof.json` | DONE (хранение) | — | локально |
| Higgsfield / Soul | генерации | платное — только с «да» | см. память владельца | OPEN | не трогать без «да» | облако платное |

Результаты других агентов:
- ⟦ЗАПОЛНИТЬ ПРИ СБОРКЕ: phone context/compact/memory/cloud.ask — ветки feat/phone-core-ctx-cloud-20261021 и feat/phone-slash-ui-20261021⟧
- ⟦ЗАПОЛНИТЬ ПРИ СБОРКЕ: fix/chat-history-after-stop-20261021⟧
- ⟦ЗАПОЛНИТЬ ПРИ СБОРКЕ: матрица tools/skills — test/tools-skills-proof-20261021⟧
- ⟦ЗАПОЛНИТЬ ПРИ СБОРКЕ: feat/doc-intake-library-20261021⟧
- ⟦ЗАПОЛНИТЬ ПРИ СБОРКЕ: feat/pult-rich-format-20261021⟧

Ни один трек не объявлен закрытым без улики; полная инвентаризация (поиск по `docs/owner/bossman_lessons_*`, `docs/owner/runpod-*`) — ⟦ЗАПОЛНИТЬ ПРИ СБОРКЕ: дополнить после полного прохода по дереву⟧.
