# Аудит дерева развития Bossman по 13 веткам — 08.10.2026 (ЧЕКПОИНТ, НЕ ЗАВЕРШЁН)

Статус: **PARTIAL / INSUFFICIENT_EVIDENCE**. Это честная точка остановки, а не результат.
Owner directive 08.10: прочитать карту развития, объяснить простыми словами, что в Bossman уже есть, чего не хватает и с чего начать; на каждую ветку по 3 агента-аудитора, по 2 open-source решения на лист; собирать только лучшее; писать только документацию, код не писать; результат положить в `main` и остановиться.

Same-product Terminal Run: CLI, дашборд и Telegram — одна поверхность одного Bossman; этот документ ничего отдельно не вводит.
Лестница North Star: по имеющимся уликам достигнут только `SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT`. Один цикл самоисправления заявлен аудитором 07.10 (цикл 23, 72/72 скрытых теста) — как `SELF_REPAIR_SINGLE_CYCLE_PASS` в реестре доказательств он **не записан**; три цикла подряд — не доказаны. Этот аудит уровень не поднимает.

## 0. Что произошло в этой сессии (чтобы следующий чат не повторял ошибки)

| Шаг | Итог |
|---|---|
| Прочитать `https://molotroka123-cell.github.io/ai/bossman-development.md` и `…/subtle-baklava-676893/` | **НЕ ПРОЧИТАНО**: домен `github.io` закрыт сетевой политикой облачного контейнера (CONNECT 403), `raw.githubusercontent.com` путей сайта не нашёл (404). Источник данных сайта найден в репозитории: `command-center/bcc/capability_tree_seed.json` (833 узла, 13 зон, `as_of 2026-10-05`, `source_sha e4539337`, ветка `feat/bossman-1.9-final-freeze`). Все цифры ниже — из него и из `docs/architecture/tree-registry.json`. На сайте по скриншоту владельца 849 узлов и другие счётчики («Работает в Bossman 164», «Есть сохранённый прогон 405») — сайт новее seed в `main`; расхождение не сведено. |
| Запустить 3 агента на ветку (13 аудиторов доказательств + 13 разведчиков OSS + 13 отборщиков) | Запущено 20 из 26 первых двух волн (лимит одновременных агентов 20). **Все 20 упали с HTTP 429 «session limit»** до записи результатов. Ни один `A1/A2/A3` файл не создан. Третья волна не запускалась. |
| Документация | Этот README, брифы `BRIEFS.md`, правки входных документов (`AGENTS.md`, `CLAUDE_NEXT_ACTION.md`, `CURRENT_STATE.md`). Код не менялся. |

## 1. Простыми словами: что в Bossman уже есть

Опора: `tree-registry.json` (head `8d4a4be0`), blue-leaf audit 06.10, прогон на ПК владельца 06.10, взгляд аудитора 07.10, `KNOWN_LIMITATIONS.md`, `CLAIMS_NOT_PROVEN.md`.

- **Есть большая кодовая база одного продукта**: Core + Command Center + Telegram-собеседник Jeff + CLI, одна память, один реестр моделей, система разрешений never/ask/allowed, бюджеты, STOP владельца. Это подтверждено прогонами тестов в облаке (bossman-core 3446 passed, command-center 8691, корень 2980 — blue-leaf audit 06.10) и установкой сборки `12e003e7` на ПК владельца с `source_identity=PASS`.
- **Есть сборка под Windows с проверкой точного SHA** (ZIP 792 МБ, sha256 записан, `BOSSMAN_BUNDLE_ACCEPTANCE=PASS`, `/health/live` показывает build_sha). Переключение сборок (Switch) работает, но не гасит старого Jeff, если тот держит lock — дефект обойдён, не исправлен.
- **Jeff живёт**: doctor PASS, бесплатные маршруты моделей (nemotron, gemma — ZERO_COST_OK), поиск через SearXNG и DDG без ключа, периметр инструментов (location/device/computer запрещены).
- **Есть инфраструктура самоулучшения**: кандидат-воркспейсы, независимый верификатор, уроки, чекпоинты, дерево возможностей со сканером без ИИ. Один цикл самоисправления заявлен аудитором 07.10; на ПК владельца 06.10 все три попытки запуска самоулучшения дали `NOT_RUN`/`failed`/`FAILED` (ученик правит лишние файлы, независимая проверка не пройдена).
- **Есть каталог open source** (121 запись) и принятая шкала отбора G·P·D·S·V с правилом «внедрять только ≥ 9/10, доказано замером». По этой шкале реально внедрено два решения: родной EPUB-парсер (идея MarkItDown) и pip-audit по поставляемому lock (нашёл pyjwt 2.14.0 — PYSEC-2026-4141, не исправлен).

## 2. Простыми словами: чего не хватает

Главный факт из реестра доказательств (`tree-registry.json`, `proven_through_counts`):

| Уровень | Листьев |
|---|---:|
| none (идея/запись/заявление без квитанции) | 67 |
| code (файл есть, тесты не сопоставлены) | 280 |
| tests (тесты по модулю прошли в облаке) | 485 |
| ci (зелёный CI на SHA с файлом улики) | **0** |
| owner_pc (лог прогона на ПК владельца) | **0** |

То есть **ни один лист не доведён до уровня «проверено на машине владельца»** в самом реестре, хотя отчёты о прогонах на ПК есть. Это не значит, что ничего не работает — это значит, что улики не привязаны к листьям.

Распределение статусов в seed (833 узла): reported 556, recorded 180, branch 32, code 31, mixed 14, blocked 11, idea 6, retired 2, prepared 1. «reported» — историческое заявление о прогоне, не факт; «recorded» — записанная ссылка/конфигурация без доказательства.

Конкретные дыры (по уже существующим аудитам, не по новому прогону):
1. **Самоулучшение**: нет записанного `SELF_REPAIR_SINGLE_CYCLE_PASS` в реестре; три цикла подряд не доказаны; локальный кодер срывает вызовы инструментов; воркеры зацикливаются на чтении.
2. **Перенос опыта после рестарта** на невиденных задачах не измерен (`LOCAL_LEARNING_GAIN_NOT_MEASURED`). План K1m6a 08.10 (`docs/trading/K1M6A_RESUME_AND_PROVE_LEARNING_20261008.md`) — `NOT_RUN`.
3. **Медиа-генерация** (Z-Image, FLUX, Wan 2.x, ACE-Step): веса на диске, пробных генераций не было, тесты только с поддельным ComfyUI.
4. **Автоответчик Jeff** (ветка `consolidate/jeff-20261007`): MERGE_WITH_FIXES, блокеры F1–F4 (STOP в движке ответов, раскрытие ИИ, обход allow-list неопознанным звонящим, полный номер в `credentials.enc`).
5. **Деньги**: исследование заработка локальной моделью — 33 % числовых утверждений подтверждены источниками; `REVENUE_CAPABLE_PILOT` не достигнут, ни одна бизнес-задача владельца не доведена до верифицированного результата.
6. **Красные гейты**: Intelligence Preservation (нужны парные замеры владельца), pyjwt уязвимость в поставляемом lock, нестабильный тест `test_agents_deeplink_stale_modal` на ПК владельца (3 из 3 FAIL против 3/3 PASS у автора фикса).
7. **Карта**: 28 исходников имеют по несколько листьев (109 листьев делят файл) — дубли карты; 23 листа без единого теста (список в blue-leaf audit); сайт (849 узлов) и seed в `main` (833) расходятся.

## 3. С чего начать (порядок, не меняющий архитектуру)

1. **Свести SHA**: сайт дерева, `capability_tree_seed.json` в `main`, установленная сборка и `tree-registry.json` должны указывать на одну ревизию. Пока этого нет, любой «зелёный» на сайте не проверяем.
2. **Привязать существующие улики к листьям**: прогон на ПК владельца 06.10 и CI-прогоны уже существуют, но `ci`/`owner_pc` в реестре = 0. Это работа инструмента `tools/tree_registry_sync.py registry --ci-evidence … --owner-evidence …`, а не нового кода.
3. **Довести один цикл самоисправления до записи в реестр** (`SELF_REPAIR_SINGLE_CYCLE_PASS`): воспроизвести цикл 23 на точном SHA установленной сборки с квитанцией (команда, код выхода, sha256 вывода). Затем три независимых цикла подряд.
4. **Закрыть F1–F4 автоответчика** до любого влива ветки Jeff в `main`.
5. **Один владельческий бизнес-кейс до конца** (сайт/лендинг, медиа-набор или документ для клиента) через штатный путь кандидат → тесты → верификация → WAIT_APPROVAL. Это единственный путь к `REVENUE_CAPABLE_PILOT`; идеи заработка таковым не являются.
6. Только после этого — оценка новых open source по шкале ≥ 9/10 с замером (раздел 5).

## 4. Карта веток: что известно без нового аудита

Листья и статусы — из seed. «Первый шаг» — из существующих аудитов, не из прогона этой сессии.

| Ветка (id) | Листьев | Статусы (seed) | Что доказано сейчас | Первый шаг |
|---|---:|---|---|---|
| Jeff · собеседник (`jeff`) | 139 | reported 129, branch 9, blocked 1 | doctor PASS на ПК 06.10, free-маршруты, поиск без ключа; 3 листа без тестов (`pit/categories.py`, `master_parser/corpus.py`, `passport_sink.py`) | F1–F4 автоответчика; живой Telegram-сценарий на итоговом SHA |
| UX и CMD (`ux`) | 33 | reported 32, blocked 1 | Terminal Run 1.2 — контракт «одна поверхность» (docs/terminal); паритет CLI/UI/Telegram не измерен | замер «одинаковая задача: CLI vs дашборд» по TERMINAL_RUN_1_2_MASTER |
| Приложения и бизнес (`apps`) | 26 | code 11, reported 10, blocked 3, idea 1, mixed 1 | тесты модулей проходят; доход 0; web-designer OSS уже оценены в OPEN_SOURCE_SOURCE_LEDGER | один бизнес-кейс владельца до WAIT_APPROVAL |
| Локальные модели (`models`) | 6 | recorded 3, reported 2, idea 1 | Strix Halo без CUDA; llama-server router mode и Parakeet TDT отмечены как «новее, нужен замер на ПК» (OSS_REFRESH) | парный замер качества/скорости на ПК владельца (Intelligence Preservation) |
| API · облака · free (`cloud`) | 14 | reported 13, idea 1 | free-only guard в реестре (1.9 freeze), free-маршруты ZERO_COST_OK | подтвердить бюджеты/фолбэки квитанциями на ПК |
| Браузер и компьютер (`computer`) | 11 | reported 9, blocked 1, idea 1 | Computer Use Windows-only (pywinauto+pyautogui), живой Notepad-прогон относится к старой линии коммитов | повторить живой Notepad/STOP на новой сборке |
| Motion и медиа (`media`) | 44 | reported 34, recorded 6, code 1, blocked 1, idea 1, retired 1 | только поддельный ComfyUI; 6 листьев без тестов (gcode, social_farm media, studio_review, web_designer_ai_build, genjutsu, motion epic) | одна реальная генерация на ПК с ffprobe и декодированием |
| Память и обучение (`memory`) | 62 | reported 53, code 4, branch 2, blocked 1, idea 1, prepared 1 | 57 синих листьев covered тестами; COACHING_PIPELINE_TESTED с MOCK-моделью; gain не измерен | K1m6a план 08.10: baseline / lessons / restart-transfer на locked test |
| Агенты и оркестрация (`agents`) | 19 | reported 19 | 15 covered, 2 без тестов (`coding_sessions.py`), 2 skipped; 3 попытки самоулучшения на ПК провалились | тест на `coding_sessions.py`; повтор bounded self-improve с квитанцией |
| Навыки (`skills`) | 50 | recorded 49, reported 1 | только записи SKILL.md; квитанций вызова навыка самим Bossman в аудитах не найдено | найти/создать квитанцию вызова одного навыка из Bossman (не из Claude Code) |
| Плагины и коннекторы (`plugins`) | 26 | code 14, reported 12 | код есть, живые внешние вызовы не задокументированы квитанциями | один живой коннектор с квитанцией через систему разрешений |
| Система и проверки (`ops`) | 268 | reported 242, branch 21, blocked 2, code 1, recorded 1, retired 1 | сборка/Switch/health PASS с логами 06.10; 11 листьев без тестов (apprentice, benchmark, cost_control, notifications, toolkit, agentmap, benchlab); pyjwt; красный Intelligence Preservation | перезаписать lock (pyjwt ≥ 2.15), привязать CI/owner-улики к реестру |
| Open source · каталог (`oss`) | 121 | recorded 121 | ~90 без кода в продукте (OSS scorecard 05.10); внедрено 2 решения по правилу ≥ 9 | провести волны A2/A3 (BRIEFS.md) — здесь не сделано |

## 5. Open source: что уже решено и что остаётся сделать

Полный проход «2 кандидата на лист × 13 веток» **не выполнен** (агенты упали по лимиту). Чтобы следующий чат не искал заново, вот уже принятые решения из репозитория:

- **Внедрено (≥ 9/10 с замером)**: родной EPUB-парсер по идее MarkItDown (10/10); `astra_security_gate.py --component windows-bundle` с pip-audit по поставляемому lock (10/10).
- **Отклонено / только референс** (OSS_ADOPTION_SCORECARD_20261005): markitdown как зависимость (6), sqlite-vec (3, ждёт решения по dense-эмбеддингам), DSPy/GEPA (2), LangMem (1), apprise (1), PySceneDetect (2), ccxt/pybit (1, торговые полномочия запрещены), openai-agents/LangGraph/pydantic-ai/CrewAI/FastMCP/Composio/Temporal (≤ 2).
- **Новее, нужен замер на ПК** (OSS_REFRESH_20261005): llama-server router mode вместо llama-swap; Parakeet TDT 0.6B v3 через onnx-asr против faster-whisper; docling остаётся лучшим по таблицам; browser-use остаётся REFERENCE_ONLY при собственном `browser_runtime.py`.
- **Web-дизайн** (OPEN_SOURCE_SOURCE_LEDGER 24.09): onlook (Apache-2.0, адаптер/референс), open-design, open-codesign (референс), uselayout (AGPL — только внешний), Build-Beautiful-Sites (source-available, не бандлить).
- Единственная проверка сети в этой сессии: WebSearch работает из облака; `github.com`/`api.github.com` из curl закрыты (403); `pypi.org` JSON доступен. Поиск по Piper TTS подтвердил: upstream переехал в `OHF-Voice/piper1-gpl`, у голосов отдельные MODEL_CARD-лицензии, ru_RU есть — это нужно учесть для листа `reg-oss_piper` (№ 1 в очереди проверки 06.10).

Правило для следующего чата: кандидат без замера не набирает V, значит почти ничто не пройдёт порог 9 «на бумаге». Результат волн A2/A3 — очередь кандидатов с планом замера, а не список «внедрить».

## 6. Как повторить аудит (для другого чата)

1. Выгрузить листья по зонам из seed (без сети, без моделей):
```bash
python3 - <<'PY'
import json,collections,os
d=json.load(open('command-center/bcc/capability_tree_seed.json'))
kids=collections.defaultdict(list)
for n in d['nodes']: kids[n.get('parent','')].append(n)
def desc(i):
    o=[]
    for c in kids[i]: o.append(c); o+=desc(c['id'])
    return o
os.makedirs('zones',exist_ok=True)
for z in kids['bossman']:
    with open(f"zones/{z['id']}.md","w") as f:
        f.write(f"# ZONE {z['id']} — {z['label']}\n")
        for n in desc(z['id']):
            f.write(f"\n## {n['id']} | {n['label']} | status={n['status']}\n{n.get('detail','')}\nnext: {n.get('next_action','')}\nsources: {n.get('sources')}\nrefs: {n.get('reference_paths')}\n")
PY
```
2. Запускать волны по брифам из `BRIEFS.md`: A1 (доказательства) и A2 (OSS) параллельно, A3 (отбор) после них. Не более 20 агентов одновременно; при ограниченных лимитах — по 3–4 зоны за раз, начиная с `memory`, `agents`, `ops`, `apps`.
3. В репозиторий класть только сводки по зонам (`zones/<zone>.md` в этом каталоге) и обновлять раздел 4 этого README. Статусы в `capability_tree_seed.json` руками не менять — только через `tools/tree_registry_sync.py` и `tools/tree_apply_evidence.py` с файлами улик.

## 7. Что этот документ НЕ утверждает

- Не утверждает, что какая-либо ветка «работает»: таблицы — пересказ существующих аудитов на их SHA.
- Не поднимает уровень North Star и не сертифицирует `main`, установленную сборку или сайт дерева.
- Не включает ни одной зависимости и не даёт разрешений на звонки, покупки, голос, торговлю или фоновые траты.
