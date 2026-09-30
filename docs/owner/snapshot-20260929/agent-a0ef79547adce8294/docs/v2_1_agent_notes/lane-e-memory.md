# Lane E — Memory / Obsidian (фаза E)

Статус: **DONE** для встроенного локального backend'а.
**PARTIAL** для `memsearch` и dense-эмбеддингов (см. «Ограничения»).

## Изменённые/добавленные файлы

Ничего из чужой зоны не тронуто (`engine.py`, `api.py`, `db.py`, `providers.py`,
`tools.py`, `approvals.py`, `pyproject.toml`, `ui/pages/index.js` — не менялись).

| Файл | Что |
|---|---|
| `command-center/bcc/v2/memory/{obsidian,memsearch_bridge,reranker,context_pack}.py` | аддон скопирован **как есть** |
| `command-center/bcc/v2/memory/service.py` | backend теперь Protocol (`MemoryBackend`), добавлены `expand/stats`, `expand_k`, `filename` в `remember`, реранкер не роняет поиск |
| `command-center/bcc/v2/memory/local_index.py` | **новое** — встроенный BM25-backend + `LexicalReranker` |
| `command-center/bcc/v2/memory/__init__.py` | экспорты |
| `command-center/bcc/features/tools_memory.py` | **новое** — Feature: 5 инструментов в `REGISTRY` + `/api/memory/*` |
| `command-center/tests/test_v21_memory.py` | **новое** — 16 тестов |
| `command-center/tests/v2/test_obsidian_memory.py` | тест аддона скопирован как есть (3 теста) |
| `.agents/skills/{memory-curator,memory-recall,obsidian-knowledge}/SKILL.md` | скопированы как есть |

`docs/CLAUDE_MEMORY_HANDOFF.md` и `docs/INTEGRATION_SNIPPET.md` из аддона
**намеренно не добавлены**: они описывают только memsearch-путь и противоречили бы
реальной интеграции (G15). Оригиналы лежат в `BOSSMAN_MEMORY_RAG_OBSIDIAN_ADDON.zip`.

## Что работает

* **Встроенный backend `LocalMemoryBackend`** (`bcc/v2/memory/local_index.py`),
  чистый stdlib, без `numpy`/`sentence-transformers`/`rank_bm25`:
  * чанкование markdown по заголовкам (путь заголовков `A > B`), длинные секции
    режутся по абзацам с перекрытием; frontmatter отбрасывается;
  * BM25 Okapi (k1=1.5, b=0.75) с грубым стеммером RU/EN (одинаково к документу
    и к запросу), заголовок и имя файла весят ×2;
  * индекс — JSON под `data_dir/memory/index-<fp>.json`, атомарная запись;
  * **инкрементально по sha256 файла**: неизменившееся пропускается; удалённые
    файлы вычищаются только в пределах просканированных корней (поэтому
    переиндексация одной папки `BOSSMAN Memory/` не сносит остальной индекс);
  * `expand(chunk_hash)` отдаёт **секцию целиком** — это и есть прогрессивное
    раскрытие.
* **Прогрессивное раскрытие и бюджет**: `memory.search` → top‑K кандидатов (по
  умолчанию 16, диапазон 4–40) → `LexicalReranker` → 8 → `expand` первых 4 →
  дедуп по отпечатку текста → `build_context_pack` режет по бюджету.
  Бюджет памяти отдельный: `max_context_tokens` (default 4000, max 12000) и
  никак не связан с контекстом задачи.
* **Инструменты в `bcc.tools.REGISTRY`, `source="memory"`**:
  | имя | эффект | примечание |
  |---|---|---|
  | `memory.search` | auto | `external_output=True` |
  | `memory.expand` | auto | `external_output=True` |
  | `memory.write` | **ask** + право `filesystem.write` | `idempotent=False` |
  | `memory.index` | auto | пишет только в data dir |
  | `memory.stats` | auto | |
  Регистрация — в `setup(svc)` фичи, идемпотентна.
* **Путь к vault только от человека**: `settings_kv["memory.vault"]`,
  значение зашифровано `svc.vault` (ровно как `terminal.roots`).
  **Автопоиска Obsidian по машине нет** и добавлять нельзя.
* **Никакой автоинъекции памяти**: хука, подмешивающего контекст в каждый вызов
  модели, не создано; тест `test_memory_is_not_injected_into_every_call` это
  фиксирует. Извлечение — только когда агент сам вызвал `memory.search`.
* **HTTP для человека**: `GET/POST /api/memory/config`, `POST /api/memory/index`,
  `POST /api/memory/search`, `POST /api/memory/expand`, `POST /api/memory/write`,
  `GET /api/memory/stats`. Не настроен vault → 503 с подсказкой, не 500.
* **Границы записи** сохранены из аддона: запись только в `BOSSMAN Memory/`,
  `.obsidian/.trash/.git/node_modules/.venv/__pycache__` не индексируются.
  Побег через `filename` (`../../evil.md`, `/tmp/evil.md`, `../notes/evil.md`)
  ловится `ObsidianVault` и приходит модели ошибкой-данными, run не падает.
* **`MemSearchBridge` остался опциональным**: `backend: "auto" | "local" | "memsearch"`.
  `auto` = memsearch, если бинарь есть в PATH, иначе local. Явный `memsearch`
  без бинаря → честная 400, конфиг при этом **не сохраняется**.

## Тесты

```
cd command-center && timeout 300 python -u -m pytest tests/test_v21_memory.py -q
→ 16 passed
cd command-center && timeout 300 python -u -m pytest tests/test_v21_memory.py tests/v2/test_obsidian_memory.py -q
→ 19 passed
cd command-center && timeout 400 python -u -m pytest -q
→ 185–188 passed (число плавает: другие лейны доливают тесты в то же дерево)
```

Что доказано на настоящем временном vault'е: индексация 3 заметок → модель
(`ToolAdapter`, стиль `tests/test_v21_tool_loop.py`) спрашивает про выбор БД,
сама вызывает `memory_search`, получает секцию `notes/architecture.md > Выбор
базы данных` с `PostgreSQL`, и **строит ответ из полученного tool‑сообщения**
(шаг `"cite"` вытаскивает источник из текста, а не подставляет заранее). Плюс:
запись только в `BOSSMAN Memory/`, ASK без права, инкрементальный ре‑индекс
(`added=0, skipped=4` на повторе; правка файла → `updated=1`), отказ на побег,
бюджет пакета, `.obsidian` не в индексе.

## Известные падения (НЕ Lane E)

При полном прогоне периодически падают чужие тесты — проверено с **вынесенным**
`tools_memory.py`, падают и без него:

* `tests/test_v21_tool_loop.py::test_ask_creates_approval_and_frees_worker`
  (строка `assert not env.svc.engine.active_run_ids`) — гонка, флак ~2 из 3 прогонов;
* `tests/test_feat_workflow.py::test_log_endpoint_is_incremental` — флак, в
  одиночном прогоне файла проходит 3/3;
* `tests/test_v21_auth.py::test_mutation_by_cookie_requires_csrf` — `UnicodeError`,
  файл другого лейна.

## Ограничения / PARTIAL

1. **memsearch (Milvus/ONNX) не проверен E2E** — бинаря в среде нет
   (`shutil.which("memsearch") is None`). Код-путь оставлен и выбирается
   конфигом; протестирован только честный отказ при отсутствии бинаря.
2. **Dense-эмбеддинги не включены** — `load_dense_encoder()` требует
   `numpy` + `sentence-transformers`, которых нет; функция честно кидает
   `DenseUnavailable`, backend работает на чистом BM25. Поле
   `LocalMemoryBackend.dense` зарезервировано под гибрид, гибридный скоринг
   пока не реализован.
3. **Cross-encoder реранк недоступен** (`LocalCrossEncoderReranker` требует
   sentence-transformers) → по умолчанию `LexicalReranker` (покрытие термов +
   попадание в заголовок). Качественно это слабее модели.
4. **Стеммер грубый** (обрезка окончаний RU/EN). Для длинных vault'ов может
   давать шум; заменяемо без смены формата индекса — но при замене поднять
   `INDEX_VERSION` (индекс с чужой версией просто перестраивается).
5. **Индекс — один JSON целиком в памяти.** Для vault'а в тысячи заметок это
   приемлемо, для десятков тысяч — нужен SQLite‑постинг. Не делал: не требуется.

## Крючки для Integration Lead

* Ничего в `db.py` не нужно: используется существующая `settings_kv`.
* Инструменты появляются у агента, только если выданы: `agents.tools` или
  `tasks.meta.allowed_tools` с `"memory.*"` (пусто ≠ «все»).
* Чтобы `memory.write` шёл без вопроса — выдать агенту право
  `filesystem.write`; иначе ASK через штатный approval (проверено).
* Скиллы `memory-recall` / `memory-curator` / `obsidian-knowledge` лежат в
  `.agents/skills/`; когда Lane 3 свяжет скиллы с наборами инструментов (G9),
  им надо отдать ровно `["memory.search", "memory.expand"]` и
  `["memory.write"]` соответственно.
* UI-страницы для памяти нет — при желании `ui/pages/memory.js` (настройка
  vault, ручной поиск, stats). Не в зоне Lane E.
