# Защищённый воркфлоу обучения Bossman на собственном датасете (23.10.2026)

Запрос владельца: «Подготовь воркфлоу под обучение на датасете что мы хотели, нужно защититься в свете
хакерских атак последних». Контекст атак 21–22.10: LiteLLM 1.107.0 (PyPI + Docker, стилер ключей + RAT),
PHP2Shell, GitLab CVSS 10, NGINX, WordPress-ключи разработчиков, AOMEI-сервер обновлений. Общий урок:
**зависимости, обновления и сторонние данные — это канал атаки**, а обучающий датасет — канал отравления.

Какой датасет «мы хотели» (инвентарь `TRAINING_CLOSEOUT_20261022_RU.md`): единственный трек, который можно
выполнить на этом ПК (AMD, без CUDA, RunPod запрещён 11.10) — **собственный корпус Bossman** из харвестера
`command-center/bcc/pit/master_parser/training_harvester.py` (Jeff KB, training-drops, consented passport-факты,
web-learn, War Terminal / OpenNews) + локальные референсы владельца (`feedback 19.10: все локальные файлы —
обучающие референсы`). Выход харвестера: `<data>/bossman-training/corpus.jsonl` + `manifest.json`.
Два режима использования корпуса:

- **Линия A, WEIGHTS_UNCHANGED (сейчас):** RAG/LessonBook/коучинг локальных Qwen через ollama. Без GPU-обучения.
- **Линия B, LoRA/SFT (когда появится железо/ROCm):** тот же корпус, тот же гейт, обучение только локально.

Ни одна линия не имеет права объявлять «научился» без измеренного прироста на holdout (North Star: recall ≠ обучение).

## Этапы и гейты

| # | Этап | Что делаем | Гейт (PASS/FAIL, улика) |
|---|---|---|---|
| 0 | Изоляция среды | Отдельный `train-venv` (Python 3.12), ставится ТОЛЬКО из `requirements-train.lock` с `--require-hashes --no-deps`, `PIP_NO_INDEX=1` после прогрева кэша колёс. Никакого `litellm`, никаких `pip install -U`. | `tools/training_secure_gate.py` → `supply_chain` + `venv` |
| 1 | Сбор | `harvest_training_corpus(data_dir)` (уже есть: injection-guard, redact_secrets, дедуп, ACL owner-only). Источники только из allow-list; web-learn кандидаты — только после ревью. | `manifest.json` с sha256 каждого файла |
| 2 | Заморозка | Копия датасета в каталог вне git `C:\Users\asd\Bossman\train-snapshots\<dataset_id>\` (ACL владельца), манифест подписывается хэшем, после этого файлы read-only. | `manifest` в гейте: нет лишних/изменённых/пропавших файлов |
| 3 | Гейт безопасности | `python tools/training_secure_gate.py --dataset <snapshot> --lock requirements-train.lock --venv <train-venv>\python.exe --require-offline --out gate_report.json` | `VERDICT PASS` в `gate_report.json` (счётчики и id, без текста) |
| 4 | Обучение | Линия A: индексация/уроки через существующие модули (`memory_retrieval_v16`, LessonBook). Линия B: SFT/LoRA только `safetensors`, базовая модель по закреплённой ревизии и sha256, `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1`, сеть выключена для процесса, никаких `pickle`/`.bin`/`trust_remote_code`. | лог запуска + sha256 базовой модели и результата |
| 5 | Оценка | Holdout (не входит в корпус), A/B с базой на 10+ неизвестных задачах, красная команда: тесты `PROMPT_INJECTION_ESCALATION_BLOCKED` и 6-vector security regression на обученной модели, проверка, что модель не воспроизводит секрет-канарейки из датасета. | числа до/после; при отсутствии разницы — `OUTCOME_UNPROVEN` |
| 6 | Промоушен | Кандидат → «да» владельца в пульте → установка; артефакт с provenance (`dataset_id`, sha256 данных, lock-хэш, базовая ревизия, команда запуска). Откат = предыдущий артефакт. | provenance.json рядом с артефактом |

## Защитные правила (почему именно так)

1. **Supply chain.** Lock с хэшами и запрет `litellm` — прямой ответ на компромисс LiteLLM (стилер активировался
   через startup-файлы Python даже без импорта пакета). Автообновления отключены (AOMEI показал, что сервер
   обновлений — тоже канал). Образы Docker не используются; `bossman-infra/compose.yaml` с `litellm:main-latest`
   остаётся непригодным до пина по digest.
2. **Отравление датасета.** Харвестер режет инъекции на входе, гейт считает их повторно на замороженном снимке и
   валит прогон при любой инъекции (`--max-injection-rate 0.0`), при секрет-подобных строках, при неизвестных
   источниках и при дублях сверх нормы. Канарейки в тестах гейта доказывают, что отчёт никогда не содержит текст.
3. **Личные данные.** Никаких фото/голоса/чатов в датасет для весов; строки с указателями на `%LOCALAPPDATA%\Bossman`,
   `keys\`, `Гречка`, `datasets\gf*`, `voice-train-*` валят гейт. Обучение только на этом ПК (запрет pods 11.10).
   Методичку Jeff и чаты Claude не читает: гейт работает по счётчикам и хэшам.
4. **Артефакты.** Только `safetensors`; базовая модель по ревизии + sha256; результат с provenance; никакого
   `trust_remote_code`. Загрузка весов из интернета — отдельный шаг с «да» владельца, не во время обучения.
5. **Проверка результата как угрозы.** Обученная модель прогоняется через те же security-тесты, что и рантайм:
   инъекции, эскалация, выдача секретов. Красный результат = артефакт не промоутится.

## Что уже есть в дереве и что добавлено

- Есть: харвестер (Argon, 22.10), `redact_secrets`, injection-guard, `tools/training_data_preflight.py` (фото/голос),
  supply-chain p1 (pinned workflow SHA, `requirements.lock` с хэшами, запрет auto pip upgrade, тесты
  `tests/test_supply_chain_and_ling_coder_security.py`).
- Добавлено 23.10: `tools/training_secure_gate.py` + `tests/test_training_secure_gate.py` (8 тестов: чистый датасет
  PASS; непинованный/запрещённый пакет, подмена файла, инъекция, секрет, чужой источник, указатель на личные
  данные, отсутствие offline-флагов — FAIL; отчёт без текста).

## Как запустить (линия A, бесплатно, сейчас)

```powershell
# 1. снимок корпуса (харвестер уже умеет ACL + manifest); затем копия в train-snapshots
# 2. гейт
$env:HF_HUB_OFFLINE='1'; $env:TRANSFORMERS_OFFLINE='1'; $env:PIP_NO_INDEX='1'
python tools\training_secure_gate.py --dataset C:\Users\asd\Bossman\train-snapshots\<dataset_id> `
  --lock command-center\requirements.lock --require-offline --out gate_report.json
# 3. при PASS — индексация/уроки; при FAIL — ничего не обучаем, отчёт в пульт
```

## Открытые решения владельца

- Линия B (LoRA/SFT): нужен ROCm-стек на AMD или другое железо; RunPod запрещён. Пока — только линия A.
- Состав allow-list источников: `web_learn` и `open_news` включать в датасет для весов или только в RAG?
- Нужен ли отдельный `requirements-train.lock` (torch/peft) — создаётся при появлении железа, через тот же гейт.
