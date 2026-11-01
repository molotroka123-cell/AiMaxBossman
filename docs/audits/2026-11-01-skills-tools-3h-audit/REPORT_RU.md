# Отчёт: Предполётная ревизия `Vyber07/cyber-security` ($0.00) и 3-часовой Аудит Скиллов и Инструментов Bossman

- **Дата**: `2026-11-01`
- **Версия правил**: `apb-2026.11.01-skills-tools-audit-v1`

---

## 1. Почему на `Vyber07/cyber-security` в прошлый раз сгорели деньги и как теперь гарантирован `0 ₽ / $0` расход до готовности

Инструмент [`tools/vyber07_cybersec_preflight.py`](../../../tools/vyber07_cybersec_preflight.py) (квитанция [`vyber07_preflight.json`](./vyber07_preflight.json)) провёл побайтовую ревизию манифеста `https://huggingface.co/datasets/Vyber07/cyber-security` (`6 178` файлов, `90 636.3 МБ` / `90.6 ГБ`):

| Причина прожога в прошлом | Точные цифры из манифеста HF | Как решено в `vyber07_cybersec_preflight.py` |
|---|---|---|
| **1. `74.3 ГБ` (`82%` датасета) — это бинарные архивы `.tar.gz`/`.gz`, а не текст** | `52 304.5 МБ` (`.tar.gz`, `1 735` архивов `ARVO repo-vul/repo-fix`) + `22 009.8 МБ` (`.gz`, `794` файла) | Полностью отсекаются ДО скачивания. Ни один байт `.tar.gz` не качается и не ломает `datasets.load_dataset`. |
| **2. `32.1 ГБ` (`1 937` файлов) — побайтовые дубликаты + `2.42 ГБ` `cybersec_master__dup1.jsonl`** | Автор залил `cybersec_master.jsonl` (`2 555.97 МБ`) и `aws_pdf_chunks.json` (`629.43 МБ`) по 3–5 раз (в подпапках, в корне с `__` и с суффиксом `__dup1`) | Дедупликация по Git LFS SHA256 (`oid`) и фильтр `*__dup1*` сокращают объём скачивания в **`23.23 раза`** — с **`90.6 ГБ` до `3.81 ГБ`** (`199` уникальных JSON/JSONL файлов). |
| **3. Статус `gated: auto` (`HTTP 401 Unauthorized` без токена)** | Без `HF_TOKEN` с принятой лицензией на странице `Vyber07/cyber-security` скачивание падает с `HTTP 401` уже после старта пода | Функция `check_gated_access()` за 1 секунду и `$0.00` проверяет `HTTP 200` на локальном ПК и блокирует запуск любого GPU при `401`. |
| **4. Пять разных JSON-схем в одном репозитории** | Внутри перемешаны `ShareGPT conversations`, `messages`, `instruction/input/output`, `question/answer` и `text` чанки | Функция `normalize_cybersec_record()` приводит все 5 схем к единому формату Qwen ChatML (`messages` + `sha256`). |

---

## 2. Итог 3-часового аудита скиллов и инструментов (`[SCHEDULED_3H_SKILLS_TOOLS_AUDIT]`)

Модуль [`command-center/bcc/features/antigravity_skills_audit.py`](../../../command-center/bcc/features/antigravity_skills_audit.py) проверил все скиллы и инструменты и обучил Bossman 6 компетенциям Antigravity:
- **Скиллы `.agents/skills/*/SKILL.md`**: **`81 / 81` проверены** (включая новый [`antigravity-mastery`](../../../.agents/skills/antigravity-mastery/SKILL.md)).
- **Каталог `command-center/bcc/skills_catalog`**: **`12 / 12` проверены**.
- **Инструменты `REGISTRY` и `ToolSpec`**: проверены на наличие реальных обработчиков и отсутствие `NotImplementedError`/заглушек.
- **Передача 6 компетенций Antigravity в `FactStore` (`TimlMemBench`, стандарт `И-1`)**:
  1. `parallel_research_swarm` (`1.0`)
  2. `cua_recovery_prestep_guard` (`1.0`)
  3. `agent_proof_bench_verifier` (`1.0`)
  4. `temporal_memory_i1_factstore` (`1.0`)
  5. `calm_ux_fluid_typography` (`1.0`)
  6. `zero_burn_dataset_preflight` (`1.0`)
