# АСТЕР, НАЧНИ ОТСЮДА — V8 «Bossman Studio» (официальный Higgsfield)

Один файл, чтобы войти в курс дела за 15 минут и начать писать код в тот же
день. Всё, что ниже, проверено инструментами 17.09.2026, а не пересказано.
Где не проверено — так и написано.

---

## 0. СРОЧНО, ПРОЧИТАЙ ПЕРВЫМ

Владелец 18.09 уточнил задачу: он строит **форк**
<https://github.com/wide-trace/open-higgsfield> на конкурс за лучший форк.
Это отдельная дорожка: **Боссман и Video Studio не трогаются**, чужой код сюда
не переносится. Постановка, границы и восемь осей выигрыша —
[FORK_CONTEST_BRIEF_RU.md](FORK_CONTEST_BRIEF_RU.md). V8 внутри Боссмана идёт
дальше без изменений.

## 1. ЧТО ТЫ ДЕЛАЕШЬ

Пишешь **V8 — Bossman Studio**: одну студию генерации изображений, видео и
звука внутри уже существующего Command Center. Это ОТДЕЛЬНАЯ линия работ: она
не трогает выпуск Windows-архива, который сейчас доводится параллельно.

Контракт этапа уже написан и лежит рядом — `docs/v8/`. Ты его исполняешь, а не
переписываешь. Если находишь в нём ошибку — правишь документ тем же коммитом,
что и код, с объяснением, почему прежняя формулировка неверна.

**«Свой Higgsfield» = своя студия и свой контроль над байтами, деньгами и
уликами. НЕ свои модели.** Веса Seedance, Kling, Veo, Soul принадлежат их
владельцам и доступны только по ключу владельца. Ни один отчёт не должен
выдавать доступ по ключу за собственную генерацию.

---

## 2. ВЕТКА И ПОРЯДОК ПУША

```
ветка: claude/bossman-final-convergence-hu2702        (единственная рабочая)
head на момент записи: 04cc5bc5                       (docs/v8 уже в ней)
PR: #64 (открыт, база night/v7-convergence-20260908)
```

* Fetch перед каждым push. В той же ветке параллельно пишет второй агент.
* **Никогда**: `force-push`, `reset`, `git add -A`, `git add .`, вторая ветка
  конвергенции, второе ядро.
* Стадируй только свои пути **явным списком**. Правило проекта —
  `.claude/skills/parallel-agent-file-ownership`.
* Коммить каждый чекпоинт, не копи неделю работы в рабочем дереве.

```bash
git fetch origin claude/bossman-final-convergence-hu2702
git add <твои пути через пробел>
git commit -F - <<'MSG'
feat(studio): <что именно сделано, по-русски, без «улучшений»>

<что измерено, что доказано тестом, что осталось OWNER_REQUIRED>
MSG
git push -u origin claude/bossman-final-convergence-hu2702   # при сетевой ошибке: 2s, 4s, 8s, 16s
```

---

## 3. ОФИЦИАЛЬНЫЙ HIGGSFIELD: ЧТО ПРОВЕРЕНО, ЧТО НЕТ

Владелец требует именно **официальный** Higgsfield. Разделяй три вещи.

### 3.1 Проверено в этой сессии (официальный MCP-сервер Higgsfield)

| Что | Результат |
|---|---|
| `models_explore action=list` | **98 моделей**: video 41, image 34, 3d 17, audio 6 |
| схема записи модели | `id, name, provider_name, description, output_type, parameters[{name, required, type, options\|min\|max, default, nullable}], medias[{name, type, roles[], max}], aspect_ratios[], tags[], supports_unlim` |
| владельцы весов | Higgsfield 20, Meshy 10, Google 9, Bytedance 7, Black Forest Labs 5, Kling 5, xAI 4, Wan 4, OpenAI 3, FAL 3, прочие |
| `get_workflow_instructions` | 16 официальных воркфлоу (ad-multiplier, brand-asset-creation, character-sheet, faceless-video, narrator, product-photoshoot, subtitles, thumbnail-generation, шесть UGC, video-editing, website-builder-flow) |
| `balance` | **`{"credits": 0, "subscription_plan_type": "free"}`** |
| `unlim.available` в каталоге | **`false`** (у 20 моделей есть `supports_unlim`, но allowance недоступен) |

**Прямое следствие, которое нельзя обойти кодом:** на официальном аккаунте
владельца сейчас 0 кредитов и бесплатный план. Любая живая генерация через
Higgsfield упрётся в это. Это `OWNER_REQUIRED` (аккаунт/оплата), **а не дефект
провайдера и не FAIL**. Отказ по кредитам обязан иметь отдельную причину
(`insufficient_credit`), отличную от 429, от отсутствующего ключа и от
недоступной модели.

### 3.2 НЕ проверено — и потому не кодируется вслепую

* REST/платформенный API Higgsfield: **base URL, формат заголовка, пути
  submit/status — не подтверждены официальным источником.** `docs.higgsfield.ai`
  в этой среде закрыт egress-прокси (`EGRESS_BLOCKED`).
* Схема `POST /{model-path}` + `GET /requests/{id}/status` +
  `Authorization: Key <id:secret>` взята из ЧУЖОГО клиента
  `wide-trace/open-higgsfield` (`b16a0efe`). У того репозитория **нет файла
  LICENSE и нет ни одного теста**. Это гипотеза о чужом API, а не контракт.

**Правило.** Официальный путь номер один — **MCP-сервер Higgsfield** через уже
существующие `bcc/v2/mcp_hub.py` и `bcc/v2/mcp_runtime.py`, с приёмкой полосой
`tools/mcp_acceptance.py` (16 проб). REST-адаптер пишется ТОЛЬКО когда владелец
даст официальную документацию или base URL; до этого запись в каталоге —
`OWNER_REQUIRED: official REST contract not supplied`. Не подбирать эндпоинты
экспериментом по чужому коду.

### 3.3 Запрещено

* Копировать код `open-higgsfield` (LICENSE нет) или его интерфейс.
* Считать `openhiggsfield.ai` источником истины об официальном Higgsfield.
* Называть доступ по ключу «своей моделью» или «своим Higgsfield» в коде,
  интерфейсе и отчётах.

---

## 4. ПРАВДА О ПРОДУКТЕ НА СЕГОДНЯ (читай как факты, они измерены)

* Проверенный кандидат — `e39610de`: архив `BOSSMAN-Windows-x64-e39610de2c99.zip`,
  782 842 928 байт, SHA-256 `a09067173b360a2c106748d6e1349a376c84e5740a469d55bab39da86ea7c0e6`,
  приёмка установленного архива 42/42, развёртка PASS,
  `BOSSMAN_ASTRA6_RELEASE_STATE=WINDOWS_RC_READY_OWNER_REQUIRED`.
* **Генерации в продукте нет.** `command-center/bcc/features/images.py`:
  `EXECUTABLE_ALIASES = frozenset({"mock-image"})` плюс `comfyui`; любая другая
  модель падает с «реальный image provider … ещё не подключён».
  `bcc/video_studio/analysis.py::generation_status()` возвращает
  `{"status": "BLOCKED", "reason": "No verified image/video generation provider
  is connected to Video Studio"}`. TTS, дубляжа, музыки нет вовсе.
* Схема параметров у Images **одна на всех** (`ImageJobIn`: 256–4096 px,
  steps 1–200, count 1–8). Схемы на модель нет — это дыра №1.
* Единой галереи нет: библиотека Images и экспорты Video Studio живут порознь.
  `bcc/run_provenance.py` пишет происхождение ПРОГОНОВ АГЕНТОВ, не картинок.
* Настоящие облачные вызовы в репозитории есть, но **вне продукта**:
  `tools/hailuo_rescue.py` и `tools/seedance_shorts.py` — OpenRouter
  `/api/v1/images`, `/api/v1/videos`, остаток по `/api/v1/auth/key`, страховка
  бюджета. Это готовый образец для адаптера.
* Ключ OpenRouter в Actions Secrets **пуст** (прогон 116 → `OWNER_REQUIRED`).

---

## 5. ЧТО УЖЕ НАПИСАНО — ВТОРОЙ РАЗ НЕ ПИСАТЬ

| Нужно | Уже есть, бери это |
|---|---|
| очередь заявок с атомарным захватом и отменой посреди рендера | `bcc/features/images.py` (`process_one`, tick 0,7 с, урок BL-066) |
| хранилище файлов и защита от выхода за корень | `bcc/v2/images_runtime.py` (`ImageStorage`, `safe_filename`) |
| локальная генерация картинок | `bcc/oss/comfyui.py` |
| выбор модели, «проба важнее каталога» | `bcc/v2/model_router.py`, `bcc/v2/capability_probe.py` |
| словарь состояний провайдера | `bcc/model_health.py` (`healthy, silent, malformed, throttled, unauthorized, provider_down, timeout, unmeasured`) |
| лестница восстановления | `bcc/reality/recovery.py` (`THROTTLED` = 429/квота/кредит) |
| разбор кодов OpenRouter | `bcc/v2/openrouter_ext.py::explain_status` (401/402/403/404/429) |
| ключи | `bcc/secrets.py` (Vault, Fernet) + `bcc/v2/openrouter_identity.py` (Vault ⟶ env, конфликты названы, в логах только `…last4`) |
| цена и локальность на каждом вызове | `bcc/provider_governance.py::GovernedAdapter` |
| бюджет | `bcc/v2/governor.py` (`cloud_budget_usd` → `stop`) |
| MCP | `bcc/v2/mcp_hub.py`, `bcc/v2/mcp_runtime.py`, полоса `tools/mcp_acceptance.py` |
| неизменяемое происхождение | `bcc/run_provenance.py` + триггер БД `_install_provenance_guard` |
| проверка байтов перед «готово» | `bcc/video_studio/read_verification.py`, `export_receipt.py` |
| план пакета кадров | `bcc/video_studio/storyboard.py` (рецепты hook/reveal/detail/benefit/cta/proof) |
| приёмка и заморозка | `tools/acceptance_registry.json`, `tools/require_acceptance_results.py`, `tools/astra6_freeze.py`, `tools/installed_ui_sweep.py` |

Новый код — только `command-center/bcc/studio/`, `tools/studio_models.json`,
страница-студия, тесты. **Новых зависимостей в `pyproject.toml` — ноль**
(`httpx` уже есть).

---

## 6. ЧИТАТЬ В ЭТОМ ПОРЯДКЕ (15 минут)

1. `docs/v8/README.md` — зачем этап и восемь свойств «лучше».
2. `docs/v8/EPOCH_8_CHARTER.md` — границы и десять принципов.
3. `docs/v8/ARCHITECTURE_AND_WORKSTREAMS.md` — **твоя карта работ W1…W8**.
4. `docs/v8/IMPLEMENTATION_PLAN.md` — фазы 0–5, начинаешь с фазы 0.
5. `docs/v8/ACCEPTANCE_AND_FREEZE_GATES.md` — чем твою работу примут.
6. `docs/final/MASTER_PROMPT_NEXT_RUN.md` §0, §2, §7, §8, §10 — правила проекта.
7. `docs/final/one-archive-audit-20260917/MASTER_PROMPT_RU.md` §1, §7, §9.
8. `docs/final/SAFETY_INVARIANTS.md` — 12 инвариантов, у каждого тест-сторож.
9. Код из таблицы §5 — по диагонали, чтобы знать, где что лежит.

Не читай `command-center/build/lib/**` — это устаревшая копия с другой ветки.

---

## 7. СЕМЬ ПРАВИЛ. НАРУШИШЬ — РАБОТУ НЕ ПРИМУТ

1. **Наблюдение ≠ улика.** `completed` от провайдера → скачать байты →
   проверить (декодирование, размеры, длительность, sha256) → запись → только
   потом «готово». Статус провайдера никогда не отображается прямо в статус
   задачи.
2. **Пять разных отказов**, и ни один не становится другим: нет/отвергнут ключ
   (`unauthorized`), 429/квота/**нет кредитов** (`throttled`,
   `insufficient_credit`), модель недоступна (`provider_down`), отказ по
   контенту (`content_policy`), битый ответ (`malformed`/`silent`).
3. **Ключи — только Vault или окружение.** Ни в коде, ни в аргументах, ни в
   журналах, ни в уликах. В API и логах — `…last4`. Старый ключ из переписки
   считается скомпрометированным и недействующим.
4. **Бесплатные маршруты по умолчанию, платного fallback нет.** Неизвестная
   цена облачной модели дисквалифицирует её для автоматического выбора. Нет
   бюджета/кредитов → `OWNER_REQUIRED`.
5. **Байты владельца не уходят молча.** Первая отправка референса провайдеру —
   после подтверждения в интерфейсе. Публичных загрузок нет (у чужого клиента
   загрузки летят в публичный Vercel Blob — у нас так нельзя).
6. **Тесты не подгоняются.** Не удалять, не пропускать, не ослаблять, не
   поднимать тайм-ауты без замера, не мокать провайдера в живой проверке, не
   писать `VERIFIED = true` руками.
7. **Ничего не включается по умолчанию.** Каждый облачный провайдер —
   выключенный `ADOPT_AS_OPTIONAL_BACKEND`, ленивый старт, остановка по
   бездействию, числа покоя до/после приложены.

---

## 8. РАЗГРАНИЧЕНИЕ ФАЙЛОВ (в ветке двое)

| Твоё (Астер, V8) | Чужое — не трогай без уговора |
|---|---|
| `command-center/bcc/studio/**` | `docs/final/**` |
| `tools/studio_models.json`, `command-center/bcc/studio/catalog.py` | `tools/astra6_freeze.py`, `tools/verify_windows_bundle.py`, `tools/bundle_evening_test.py` |
| `command-center/ui/pages/images.js` (становится «Студией») | `tools/build_windows_bundle.py`, `tools/windows_bundle_lock*` |
| `command-center/tests/test_studio_*.py`, `tests/test_studio_*.py` | `.github/workflows/windows-bundle.yml` |
| `docs/v8/**` | `command-center/tests/test_web_designer_*`, `test_bundle_evening_test.py` |

`tools/acceptance_registry.json` — общий: правь **только** свою строку модулей
и минимум, одним коммитом, сразу после того как тесты позеленели.

---

## 9. ПЕРВЫЙ КОММИТ — ФАЗА 0, БЕЗ ИЗМЕНЕНИЯ ПОВЕДЕНИЯ ПРОДУКТА

1. **Замер до всего** (правило §7 проекта: без базовых чисел интеграцию не
   принимают). Запиши в `docs/v8/METRICS_BASELINE.md`: время `import bcc.app`,
   RSS и CPU покоя процесса с очередью Images за 60 с, время первого ответа
   `GET /api/images/models`. Числа — настоящие, полученные запуском.
2. `tools/studio_models.json` + `command-center/bcc/studio/catalog.py`:
   загрузчик с самопроверкой и CLI (`--json`, `--check`), по образцу
   `tools/acceptance_registry.py`. Первые записи — ровно те, что уже вызывались
   кодом репозитория: `comfyui:*`, `openrouter:google/gemini-2.5-flash-image`,
   `openrouter:google/gemini-3.1-flash-image`, `openrouter:minimax/hailuo-3-max`,
   `openrouter:bytedance/seedance-2.0-mini`. Цены — `null` (не измерены).
   Для Higgsfield — одна запись-заглушка со статусом
   `OWNER_REQUIRED: official REST contract not supplied`, без эндпоинтов.
3. `command-center/bcc/studio/provider.py`: протокол `submit/status/cancel/fetch`,
   состояния и причины, отображённые на `bcc/model_health.py`.
   `bcc/studio/providers/comfyui.py` — обёртка существующего провайдера.
4. **Тесты первыми, парами**: `tests/test_studio_catalog.py` (валидность,
   уникальность id, чужое значение настройки отвергнуто с именем поля) и
   `command-center/tests/test_studio_provider_states.py` — законный ответ
   проходит; 401, 429, 402/нет кредитов, 404, nsfw, битый JSON дают **шесть
   различимых причин**, и ни одна не проходит как `completed`.
5. Строка в журнале: `BOSSMAN_STUDIO_CATALOG=<n> verified=<k> unverified=<m>`.

Продукт после фазы 0 ведёт себя ровно как раньше. Это осознанно.

---

## 10. ЧЕМ ПРОВЕРЯТЬ ПЕРЕД ПУШЕМ

```bash
# твои тесты Command Center
python -m pytest -c command-center/pyproject.toml command-center/tests/test_studio_provider_states.py -q --timeout=180
# корневые
python -m pytest tests -q --timeout=120
# реестр skip-маркеров (обязателен, если появились новые skip)
python tools/skips_registry.py --check
# каталог студии
python command-center/bcc/studio/catalog.py --check
```

Правь `tools/acceptance_registry.json` только когда модуль уже зелёный:
минимум растёт ровно на число новых сценариев, и тот же коммит расширяет
фильтр путей `.github/workflows/windows-bundle.yml`, если добавился файл,
исполняемый на установленном архиве.

---

## 11. ФОРМАТ ТВОЕГО ОТВЕТА ВЛАДЕЛЬЦУ (коротко, без воды)

```
STUDIO PHASE: <0..5>
ВЕТКА / SHA: claude/bossman-final-convergence-hu2702 / <sha>
STUDIO CATALOG: n моделей, verified k, unverified m
ТЕСТЫ: <модуль>: N passed (команда, которой получено)
IDLE COST: import / RSS / CPU / first-answer — до → после
HIGGSFIELD: MCP <вердикт полосы или NOT_RUN> / REST OWNER_REQUIRED (нет официального контракта) / аккаунт: credits=0, plan=free → живая генерация OWNER_REQUIRED
ЧТО ОСТАЛОСЬ OWNER_REQUIRED: <список>
СЛЕДУЮЩИЙ ШАГ: <одна строка>
```

Не заканчивай планом. Не останавливайся из-за отсутствия ключа или кредитов:
репозиторная часть каждой фазы делается целиком на заглушках, живая часть
честно помечается `OWNER_REQUIRED`.

**Главный принцип:** каждая строка отчёта — измерена, а не написана.
