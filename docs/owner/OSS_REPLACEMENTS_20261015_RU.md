# OSS вместо своего: что из запланированного взять готовым — 15.10.2026

Правило владельца: «бери рабочий open source и укрепляй его, своё не строй». Документ отвечает на вопрос:
какие запланированные, недостроенные или висящие в бэклоге способности Bossman можно закрыть готовым
поддерживаемым проектом. Это исследование. Ничего не установлено, не подключено и не закоммичено.

Ветка, по которой смотрел: `unify/main-20261015` (worktree `wt-unify-20261015`).

## Как искал

- **Пробелы.** Взяты из `docs/architecture/tree-registry.json`: 900 листьев, у 95 `levels.code.state = MISSING`.
  Дополнительно учтены листья со статусами `idea`, `prepared` и `blocked` при наличии кода. Остальные источники:
  `integration-backlog.json` (curated), `POST_FREEZE_BACKLOG.md`, `KNOWN_LIMITATIONS.md`, `CLAIMS_NOT_PROVEN.md`,
  `docs/owner/OPEN_PROBLEMS_20261010.md`, `CONTINUATION_20261015_RU.md`, `MP21_NORTH_STAR_TREE_20261011.md`,
  `SKILLS_AND_OSS_SHORTLIST_20261007.md`, `OSS_ADOPTION_SCORECARD_20261005.md` и `OSS_REFRESH_20261005.md`.
- **Что уже внедрено.** Проверено по `pyproject.toml`, `docs/hybrid/sources.lock.json` и `git grep` по коду.
  Уже стоят: pytgcalls/telethon, silero-vad, piper, chatterbox, LiteLLM (только в bossman-core), Windows-MCP,
  OpenHands (apprentice), FaceFusion, SAM2 (tools), yt-dlp, faster-whisper (extra), docling, qdrant-client,
  playwright, OpenTimelineIO, GrapesJS, markitdown-mcp и uv. Повторно они не предлагаются.
- **Активность и лицензии.** Сняты 15.10.2026 через GitHub API (`pushed_at`, `license.spdx_id`, `archived`).
  Факты, которые API не даёт (ROCm/Windows, сделки, инциденты), проверены веб-поиском; источники внизу.
- **Лицензии в контексте.** Сам Bossman распространяется под **AGPL-3.0-only** (`LICENSING.md`). Поэтому GPL и AGPL
  у зависимостей совместимы с распространением. Отдельно помечаю **некоммерческие** лицензии (они запрещены)
  и лицензии «с ограничениями» (ELv2, OpenRAIL).

Условия отбора: Windows 11 и AMD Radeon 8060S (gfx1151, без CUDA), только бесплатные облака,
коммерческое использование разрешено, активность за последние примерно 6 месяцев.

## Сводная таблица (22 способности, по убыванию пользы к затратам)

Ячейка «Активность» — дата последнего push в GitHub на 15.10.2026. Усилия: S — до дня, M — 2–5 дней, L — неделя и больше.

| # | Способность (листья) | Что есть сейчас | Рекомендация (лицензия · активность · Windows/AMD) | Запасной вариант | Усилия | Риск |
|---|---|---|---|---|---|---|
| 1 | 24–48 ч непрерывной работы, watchdog и перезапуск (`cap-6`, `cap-11`, North Star 24H/48H, B8) | Свои supervisor, lease и stop в `bcc/autonomy/*`; heartbeat описан только в отчётах; сервиса Windows нет | **[WinSW](https://github.com/winsw/winsw)** (MIT · 30.07.2026 · родной для Windows) — служба с `onfailure restart`. Вместе с ним **[Uptime Kuma](https://github.com/louislam/uptime-kuma)** (MIT · 15.10.2026 · Node, работает на Windows) — push-мониторы heartbeat с уведомлением в Telegram | Планировщик Windows (встроенный) | S | Низкий. Ветка v3 WinSW долго в pre-release, брать стабильную v2.x. Kuma — ещё один процесс, но изолированный |
| 2 | Локальные модели: restart, fallback и переключение (`cap-44`, `cap-15…19`, `pit/model_route`, `model_policy`, `resilient_chat`) | Ollama, llama.cpp, своя маршрутизация; llama-swap в каталоге (`oss-11`) | **[Lemonade](https://github.com/lemonade-sdk/lemonade)** (Apache-2.0 · 15.10.2026) — сервер AMD. На Windows умеет llama.cpp ROCm и Vulkan для gfx1151, API OpenAI, Ollama и Anthropic, там же whisper и TTS | Режим router у **[llama-server](https://github.com/ggml-org/llama.cpp)** (MIT · 15.10.2026) или **[llama-swap](https://github.com/mostlygeek/llama-swap)** (MIT · 15.10.2026) | M | Средний. LocalAI отклонили за то, что он скрывает жизненный цикл моделей от губернатора памяти; к Lemonade тот же вопрос. Решение — только замер на Strix Halo |
| 3 | Единый прокси облачных моделей, лимиты и аудитор токенов (`cap-24`, `s0710-oss-3`) | LiteLLM подключён только в bossman-core (`bossman-infra/litellm`). У command-center свой registry с гардом free-only. Аудитора нет | **[LiteLLM proxy](https://github.com/BerriAI/litellm)** (MIT для ядра, папка `enterprise` под своей лицензией · 15.10.2026) — завести через него и command-center: бюджеты на ключ, журнал расходов, отчёт по токенам. Гард free-only оставить в Bossman | Свой registry плюс подсчёт через OpenLLMetry (строка 5) | M | **Высокий по цепочке поставки.** 24.03.2026 на PyPI вышли заражённые 1.82.7 и 1.82.8 (кража ключей, TeamPCP). Ставить только по хешу из lock и по digest Docker-образа, обновлять вручную |
| 4 | Регрессия промптов, навыков и уроков Qwen (`mod-eval_engine`, `s0710-oss-2`, `cap-45` retention) | Свой `eval_engine` есть только на другой ветке; retention-гейт свой | **[Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai)** (MIT · 15.10.2026 · чистый Python, умеет Ollama и OpenAI-совместимые API) — наборы задач, скореры, журнал прогонов | **[promptfoo](https://github.com/promptfoo/promptfoo)** (MIT · 15.10.2026; с 09.03.2026 принадлежит OpenAI) или **[DeepEval](https://github.com/confident-ai/deepeval)** (Apache-2.0 · 15.10.2026) | M | Низкий. Наборы и пороги пишем сами, фреймворк только гоняет их |
| 5 | Трассировка, observatory и дашборд расходов (`mod-trace_recorder`, `mod-observatory`, `cap-24`) | Модулей в этом SHA нет. Weave в каталоге только как референс | **[Langfuse](https://github.com/langfuse/langfuse)** (MIT для ядра, `ee` отдельно · 15.10.2026; с 16.01.2026 принадлежит ClickHouse) — self-host через Docker. Инструментирование — **[OpenLLMetry](https://github.com/traceloop/openllmetry)** (Apache-2.0 · 13.10.2026) | **[Arize Phoenix](https://github.com/Arize-ai/phoenix)** — один контейнер на SQLite, намного легче. Лицензия **ELv2**: не OSI, self-host разрешён, перепродавать как сервис нельзя | M | Средний. Langfuse тянет Postgres, ClickHouse, Redis и S3 — тяжело для домашнего ПК. Если тяжесть важнее лицензии, брать Phoenix |
| 6 | Звонки Jeff: определение конца реплики, перебивание, эхо (`cap-10`, `telegram_calls/audio/*`, `speech/*`) | pytgcalls, telethon и silero-vad уже стоят. Свои `echo`, `endpointer`, `playout` — около 560 строк | **[Pipecat](https://github.com/pipecat-ai/pipecat)** (BSD-2 · 15.10.2026) для конвейера и **[Smart Turn v3](https://github.com/pipecat-ai/smart-turn)** (BSD-2 · 29.01.2026). Smart Turn — модель 8 МБ на CPU, около 12 мс, **русский поддерживается** | Оставить свой конвейер и заменить только `endpointer` на ONNX-модель Smart Turn | M (только Smart Turn — S) | Средний. Готового транспорта Pipecat для pytgcalls, судя по всему, нет: свой адаптер нужен (не проверено) |
| 7 | Русская речь в текст для звонков, видео и YouTube (`speech/stt`, `cap-31`, `video_studio.transcribe`) | faster-whisper в extra, решение по нему отложено. На AMD CTranslate2 работает только на CPU | **[onnx-asr](https://github.com/istupakov/onnx-asr)** (MIT · 09.10.2026) вместе с **[GigaAM v3](https://github.com/salute-developers/GigaAM)** (MIT · 17.08.2026) — ONNX на CPU или DirectML, модель сильна в русском | **[whisper.cpp](https://github.com/ggml-org/whisper.cpp)** (MIT · 12.10.2026) с Vulkan на 8060S — многоязычный | S | Низкий. GigaAM знает только русский; для чешского нужен Parakeet v3 через тот же onnx-asr (лицензию весов не проверял) |
| 8 | Гигиена секретов: ключи и vault не попадают в git и логи (`s0710-top-07`, правило «vault never pushed») | Свой `tools/ci_secret_scan.py` — 322 строки | **[gitleaks](https://github.com/gitleaks/gitleaks)** (MIT · 30.09.2026 · один exe для Windows) — pre-push хук и шаг CI со своими правилами под vault и DPAPI. Свой сканер оставить вторым слоем | TruffleHog (AGPL; активность не проверял) | S | Низкий. Бинарник брать по контрольной сумме: TeamPCP атаковал как раз инструменты безопасности (Trivy) |
| 9 | Воркер самоисправления (`cap-35`, `s0710-oss-4/5`, `d0710-selfrepair-cycle14-15`, `mod-candidate_generator`) | OpenHands-клиент в `bossman/apprentice/*` уже есть (тяжёлый, нужен Docker) | **[mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent)** (MIT · 12.10.2026) — около 100 строк ядра, модели через LiteLLM, в том числе локальные и `:free`. Удобен как лёгкий воркер с журналом | **[OpenHands](https://github.com/OpenHands/OpenHands)** (MIT для ядра · 15.10.2026; уже интегрирован). **[Aider](https://github.com/Aider-AI/aider)** (Apache-2.0) с 22.05.2026 без push — проект замедлился | S–M | Средний. Агент исполняет bash, на Windows нужен Git Bash или своё окружение; песочница и STOP остаются наши |
| 10 | Wan2.2-Animate и видео локально на Radeon (`reg-wan_animate_rocm`, V8) | Сбой HIP launch на ROCm под Windows (подозрение на TDR) | **[ComfyUI](https://github.com/Comfy-Org/ComfyUI)** (GPL-3.0 · 15.10.2026) — официальная поддержка ROCm на Windows с Desktop v0.7.0 (ROCm 7.1.1); у AMD есть отдельная инструкция для 8060S. Своим скриптам — колёса PyTorch **[TheRock](https://github.com/ROCm/TheRock)** для gfx1151 (MIT · 15.10.2026) | Ночные сборки `rocm.nightlies.amd.com/v2/gfx1151/` | M | Средний. TDR всё равно настраивает владелец (TdrDelay). Wan 14B на iGPU медленный. GPL-3 совместима с AGPL |
| 11 | Дообучение LoRA на Windows и AMD (`cap-36`, `pv-lora-train`, PR #109 Soup) | `env_check` показывает blocked; Soup рассчитан на CUDA; RunPod запрещён | **[Unsloth](https://github.com/unslothai/unsloth)** (Apache-2.0 · 15.10.2026). По документации Unsloth и AMD поддерживает Strix Halo на Windows, WSL и Linux и сам ставит ROCm-сборки PyTorch | **[PEFT](https://github.com/huggingface/peft)** (Apache-2.0 · 15.10.2026) на колёсах TheRock или **[LLaMA-Factory](https://github.com/hiyouga/LlamaFactory)** (Apache-2.0 · 10.10.2026) | M | Средний. Поддержка Windows и Strix Halo — заявление вендора, у нас не замерено. Soup (#109) с этим конфликтует: имеет смысл заменить его на Unsloth |
| 12 | Управление Windows (`cap-27`, `s0710-top-01/04/05/08/10`, `module selector_repair`) | Windows-MCP стоит как опциональный бэкенд за портом DesktopRuntime; pywinauto | Оставить Windows-MCP (MIT · 15.10.2026). Как сильную альтернативу или референс взять **[microsoft/UFO](https://github.com/microsoft/UFO)** (MIT · 14.10.2026) — гибрид UIA и зрения, родной для Windows | **[UI-TARS-desktop](https://github.com/bytedance/UI-TARS-desktop)** (Apache-2.0 · 09.10.2026; в каталоге). **[OmniParser](https://github.com/microsoft/OmniParser)** — код CC-BY-4.0, веса icon_detect по памяти под AGPL (не перепроверял) | M | Средний. Любой бэкенд — только за нашим портом с отпечатком цели и STOP. В UFO свой агентный цикл, тот же упрёк, что к browser-use |
| 13 | Браузер через CDP и 10 подготовленных навыков (`s0710-top-02`, `skill-browser-*`) | playwright стоит; playwright-mcp оставлен референсом (15 из 16 проб прошли) | **[chrome-devtools-mcp](https://github.com/ChromeDevTools/chrome-devtools-mcp)** (Apache-2.0 · 15.10.2026) — подключение к уже открытому Chrome владельца, сеть, консоль, производительность | **[playwright-mcp](https://github.com/microsoft/playwright-mcp)** (Apache-2.0 · 08.10.2026) | S | Средний. Доступ к живому профилю означает доступ к cookie. Только за approval-портом и списком доменов |
| 14 | Фон, маски и матирование без некоммерческой SegFormer (V9, `reg-video_stage4_background`, `reg-video_stage2_clothes`) | В масках SegFormer (лицензия NVIDIA некоммерческая); SAM2 в tools | **[BiRefNet](https://github.com/ZhengPeng7/BiRefNet)** (MIT · 11.10.2026) — вырезание фона и волос. **[SAM 2.1](https://github.com/facebookresearch/sam2)** (Apache-2.0 · 30.05.2026) — трекинг масок. Оба запускаются через ONNX или DirectML либо torch на ROCm | RobustVideoMatting (GPL-3; не проверял) | M | Низкий по лицензии. Качество на одежде нужно мерить своими гейтами |
| 15 | Мимика, Stage 3 «Mimics 2.0» (`reg-video_stage3_mimics`, V7) | Модуля нет | **[LivePortrait](https://github.com/KlingAIResearch/LivePortrait)** (код и веса MIT · 01.06.2026). **Детектор лиц InsightFace — только некоммерческий**, для продукта заменить на MediaPipe | Expression restorer в FaceFusion (основан на LivePortrait; лицензия OpenRAIL-AS) | M | **Лицензия.** InsightFace (buffalo_l) нельзя в коммерцию; это касается и FaceFusion, которая уже стоит. Отдельная задача — аудит используемых ей моделей |
| 16 | YouTube и K1m6a: знания и навыки (`cap-31`) | yt-dlp стоит, `tools/k1m6a_youtube_batch.py` | Сначала субтитры: **[youtube-transcript-api](https://github.com/jdepoix/youtube-transcript-api)** (MIT · 10.09.2026), без скачивания видео. Ключевые кадры — **[PySceneDetect](https://github.com/Breakthrough/PySceneDetect)** (BSD-3 · 12.10.2026). Речь — строка 7 | Только yt-dlp и STT | S | Средний. YouTube блокирует IP и меняет API, это бьёт по обоим инструментам |
| 17 | Разбор трендового эдита: склейки и бит (`reg-trend_edit_analyzer`) | Своя сетка бита на numpy и ffmpeg (`tools/trend_edit/beat_grid.py`) | **[beat_this](https://github.com/CPJKU/beat_this)** (MIT · 28.05.2026) — биты и сильные доли (torch, CPU). Склейки — PySceneDetect | librosa (ISC; не проверял). madmom **не брать**: модели CC-BY-NC-SA | S | Низкий |
| 18 | Гейты качества видео (`reg-video_animation_gate`, V1) | Свои метрики: кадры, пиксели, дрожание | Добавить стандартную метрику **[VMAF](https://github.com/Netflix/vmaf)** (BSD-2-Clause-Patent · 12.10.2026) через `ffmpeg libvmaf`. Временные метрики лица оставить свои | — | S | Низкий. Честно: готового OSS для «дрожания похожести лица» нет, своё здесь оправдано |
| 19 | Память дерева и когнитивная память (`cap-37`, `cognitive/memory`, `j2/memory_palace`) | qdrant-client в extra; sqlite-vec ждёт решения владельца по dense-эмбеддингам; mem0, graphiti и letta в каталоге | **[sqlite-vec](https://github.com/asg017/sqlite-vec)** (Apache-2.0 или MIT · 18.05.2026) — встроенный векторный поиск в той же SQLite, без сервера. Эмбеддинги через Ollama или Lemonade | qdrant (уже есть). **[Graphiti](https://github.com/getzep/graphiti)** (Apache-2.0 · 15.10.2026) — только если нужен временной граф (нужны Neo4j или FalkorDB) | S–M | Средний. sqlite-vec замедлился: последний push 5 месяцев назад. Блокер — решение владельца, не технология |
| 20 | Исследования Jeff с защитой от SSRF (`j2/research`, B2) | Своя функция research; SearXNG в каталоге | **[crawl4ai](https://github.com/unclecode/crawl4ai)** (Apache-2.0 · 15.10.2026) — загрузка страниц и чистый markdown. Поиск — SearXNG | markitdown-mcp (уже принят) для документов | S | Средний. Проверка SSRF и доменов остаётся нашей, crawl4ai ставить за ней |
| 21 | Эволюция промптов по метрике (`s0710-oss-1`, `oss-4`) | GEPA в каталоге, кода нет. Scorecard: «нужен живой замер LLM» | **[GEPA](https://github.com/gepa-ai/gepa)** (MIT · 13.10.2026) напрямую или через **[DSPy](https://github.com/stanfordnlp/dspy)** (MIT · 15.10.2026, `dspy.GEPA`). Набор оценки — из строки 4 | — | M | Средний. Много токенов, бесплатные модели упрутся в 429; без набора оценки (строка 4) бесполезно |
| 22 | 3D-модели для игр (`skill-50`) | Только SKILL.md, пути нет | **[TripoSR](https://github.com/VAST-AI-Research/TripoSR)** (MIT · 04.06.2026) — картинка в меш, работает на CPU и любом torch | Hunyuan3D — **лицензия Tencent исключает ЕС, Великобританию и Южную Корею** (по памяти; если владелец в ЕС, не брать). TRELLIS — по памяти только CUDA | S–M | Средний. Качество TripoSR ниже современных моделей; для печати есть свой `ai-3d-maker` |

## Короткие заметки

1. **Watchdog (1)** закрывает ступень North Star 24H/48H дешевле всего. Свой supervisor остаётся. WinSW отвечает
   за «процесс жив после падения и перезагрузки». Kuma отвечает за «heartbeat пришёл, иначе сообщение в пульт».
2. **Lemonade (2)** — проект AMD, сделанный под Strix Halo. По публичным замерам Vulkan даёт больше
   токенов в секунду, а ROCm — меньшую задержку первого токена. Его надо мерить против текущей связки Ollama и llama.cpp
   на одинаковых 20 запросах (протокол в `OSS_REFRESH_20261005.md`). Перед внедрением ответить на вопрос, почему
   отклонили LocalAI: кто владеет памятью.
3. **LiteLLM (3)** уже в продукте, поэтому речь о расширении, а не о новой зависимости. Без закреплённого хеша
   не ставить из-за мартовского инцидента 2026 года. Аудитор токенов (`cap-24`) тогда получается из `/spend/logs`, а не из своего кода.
4. **Inspect AI (4) раньше GEPA (21).** Без воспроизводимого набора оценки эволюция промптов и «уроки Qwen»
   (по памяти: recall 100%, исход не меняется) ничего не докажут.
5. **Звонки (6).** Самая ценная часть — Smart Turn v3: она заменяет самодельный endpointer, знает русский и работает на CPU.
   Pipecat целиком — только если переписывать конвейер.
6. **Лицензии, которые нужно разобрать в уже внедрённом.** InsightFace внутри FaceFusion и LivePortrait — некоммерческая.
   FaceFusion — OpenRAIL-AS: коммерция разрешена, но с ограничениями использования. SegFormer — некоммерческая (V9).
   piper1-gpl — GPL-3.0 (совместима с AGPL, но это не MIT, как могло казаться по старому `rhasspy/piper`).
7. **Чего хорошего в OSS нет.** Нет легального Twitter/X (`mod-twitter`): официальный API платный, скрейперы
   нарушают ToS — не рекомендую. Нет метрики дрожания похожести лица (18). Нет готовой замены своим модулям
   `cognitive/*` и `reality/*`. Если их восстанавливать, то на LangGraph (MIT · 15.10.2026) с SQLite-checkpointer
   и `interrupt()`. Но scorecard честно даёт LangGraph ≤ 2/10: измеренного пробела нет, а свой autonomy
   (constitution, journal, lease) уже покрыт тестами. Второй рантайм агентов не заводить.
8. **Что из каталога `oss` лучше не трогать.** browser-use, LocalAI и fastembed уже отклонены замерами, новых фактов против этих решений нет.
   Aider замедлился. sqlite-vec тоже, но альтернативы такой же лёгкости нет.

## Что проверено, а что нет

Проверено 15.10.2026:
- GitHub API для всех 53 репозиториев таблицы: лицензия SPDX, дата push, флаг archived.
- Веб-поиском: инцидент LiteLLM 24.03.2026; покупка Promptfoo компанией OpenAI (09.03.2026); покупка Langfuse компанией ClickHouse
  (16.01.2026, ядро остаётся MIT); ELv2 у Phoenix; ROCm на Windows в ComfyUI (v0.7.0, ROCm 7.1.1); колёса TheRock
  для gfx1151; заявленная поддержка Strix Halo в Unsloth; Lemonade на Windows с ROCm и Vulkan; Smart Turn v3 с русским;
  OpenRAIL-AS у FaceFusion; некоммерческая лицензия InsightFace в LivePortrait.

**Не проверено** (по памяти или вывод без замера):
- лицензия весов Parakeet TDT v3;
- лицензия весов OmniParser icon_detect (AGPL);
- исключение ЕС в лицензии Hunyuan3D;
- то, что TRELLIS работает только на CUDA;
- лицензии madmom, RobustVideoMatting и librosa;
- статус pre-release у WinSW v3;
- отсутствие транспорта pytgcalls в Pipecat;
- поддержка SQLite в DBOS (поэтому DBOS в таблицу не вошёл).

Ни один кандидат **не запускался на ПК владельца**. Все утверждения о Windows и AMD — со слов вендоров и сообщества.

## Источники

- LiteLLM: [NetSPI](https://www.netspi.com/blog/executive-blog/ai-ml-pentesting/litellm-supply-chain-compromise/), [pypistats](https://pypistats.com/blog/litellm-pypi-supply-chain-compromise-how-a-popular-llm-proxy-became-a-credential-stealing-backdoor-march-24-2026)
- Promptfoo: [OpenAI](https://openai.com/blog/openai-to-acquire-promptfoo), [Security Boulevard](https://securityboulevard.com/2026/03/openai-acquires-security-startup-promptfoo-to-fortify-ai-agents)
- Langfuse: [ClickHouse](https://clickhouse.com/blog/clickhouse-acquires-langfuse-open-source-llm-observability)
- Phoenix: [лицензия](https://arize.com/docs/phoenix/self-hosting/license)
- ComfyUI на ROCm: [Comfy](https://blog.comfy.org/p/official-amd-rocm-support-arrives), [AMD ROCm blog (8060S)](https://rocm.blogs.amd.com/artificial-intelligence/comfyui-windows/README.html)
- TheRock для gfx1151: [релизы](https://github.com/scottt/rocm-TheRock/releases/v6.5.0rc-pytorch), [Wan на 8060S](https://lilting.ch/en/articles/radeon-8060s-rocm-fastwan-wan-i2v)
- Unsloth на AMD: [документация](https://unsloth.ai/docs/basics/amd), [AMD](https://www.amd.com/en/developer/resources/technical-articles/2026/train-and-run-models-on-amd-gpus-with-unsloth.html)
- Lemonade: [обзор](https://lilting.ch/en/articles/amd-lemonade-local-ai-gpu-npu-server), [Phoronix ROCm vs Vulkan](https://www.phoronix.com/review/lemonade-vulkan-rocm/2)
- Smart Turn v3: [Daily](https://www.daily.co/blog/announcing-smart-turn-v3-with-cpu-inference-in-just-12ms/)
- FaceFusion OpenRAIL-AS: [licenses.ai](https://www.licenses.ai/blog/2022/8/18/naming-convention-of-responsible-ai-licenses)
- LivePortrait и InsightFace: [ComfyUI-LivePortraitKJ](https://github.com/kijai/ComfyUI-LivePortraitKJ)
