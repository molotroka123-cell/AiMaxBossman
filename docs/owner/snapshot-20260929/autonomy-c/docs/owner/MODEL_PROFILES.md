# Профили локальных моделей и загрузчик — `model_profiles.json` + `model_fetch.py`

Дата: 2026-09-22. Режим: CLOUD_PREPARE — на ПК владельца сейчас ничего не скачано и не изменено.
Машина владельца: AMD Strix Halo (Radeon 8060S, gfx1151), 128 ГБ общей памяти, Windows, llama.cpp b10964 Vulkan.

Файлы:
- `tools/model_profiles.json` — профили (что ставить, из какого источника, какие файлы, хэши, лицензия, рантайм);
- `tools/model_fetch.py` — загрузчик/проверщик (только stdlib, Python 3.11+, работает и из `app-support\`, и из checkout);
- `tests/test_model_fetch.py` — тесты на локальном http.server (Range / без Range / редирект / обрыв).

## Главное честно

**Ни один файл модели в этом манифесте не закреплён хэшем.** Из облачной сессии запросы к
`huggingface.co` (и к `api.github.com`) отклонил прокси по политике организации (HTTP 403 на CONNECT).
Поэтому у каждого скачиваемого файла `status: UNPINNED`, `sha256: null` и записана причина. Ни один
хэш, размер или ревизия не выдуманы. Единственное реально наблюдённое значение — префикс
`322e194f` у скачанного 22.09 Qwen3.8-27B UD-Q4_K_M (из CHECKPOINT-LAB); он **проверяется**: файл,
чей sha256 не начинается с `322e194f`, получает FAILED_HASH.

Закрепить хэши можно на ПК владельца одной командой `pin` (см. ниже): она читает тот же HF API
(ревизия-коммит, размер и LFS-sha256 каждого файла) и пишет закреплённую копию манифеста.
Загрузчик **отказывается** ставить UNPINNED-файл как проверенный: без `--allow-unpinned` —
`UNPINNED_REFUSED`; с флагом — скачает, но результат `UNVERIFIED_TOFU` и код выхода 5, не 0.

## Профили

| id | категория | роль | рантайм | закреплено? | обязателен |
|---|---|---|---|---|---|
| `baseline-installed` | llm_coding_agents | MAIN Qwen3.8-27B UD-Q5_K_M (:8081), FAST Qwen3.6-35B-A3B UD-Q5_K_M (:8082), GPT-OSS-120B MXFP4 2 части (:8083) + mmproj | llama.cpp b10964 Vulkan | UNPINNED (установлены, полный sha256 не записывался) · **reuse_only** | да |
| `qwen3.8-27b-ud-q4_k_m-installed` | llm_coding_agents | MAIN-кандидат, скачан 22.09 | llama.cpp b10964 | UNPINNED, префикс `322e194f` проверяется · reuse_only | нет |
| `qwen3.8-27b-q4_k_xl` | llm_coding_agents | MAIN-кандидат B (другой квант!) | llama.cpp b10964 | UNPINNED (HF 403) | нет |
| `qwen3.6-35b-a3b-fp8` | llm_coding_agents | FAST-кандидат FP8 (safetensors) | FP8-рантайм (vLLM/SGLang ROCm) — **не llama.cpp** | UNPINNED | нет |
| `qwen3.8-flash-next-q3_k_xl` | llm_coding_agents | тяжёлый мозг (MoE) | **собственный форк llama.cpp (qwen4exp)** | UNPINNED; форк не выбран | нет |
| `glm-5.3-flash-strix-balanced` | llm_coding_agents | доп. тяжёлая стадия (волна 3) | отдельный совместимый рантайм | UNPINNED; источник кванта не найден | нет |
| `snowllm-0.3.2` | llm_coding_agents (kind runtime) | альтернативный рантайм | SnowLLM | UNPINNED; версия 0.3.2 не подтверждена | нет |
| `studio-photo` | photo | ссылка на `sdcpp:z-image-turbo`, `flux1-schnell`, `flux2-klein-4b`, `sdxl-base` | stable-diffusion.cpp | REFERENCE — хэши в медиа-`MANIFEST.json` | нет |
| `studio-video` | video | ссылка на `sdcpp:wan2.2-ti2v-5b` | stable-diffusion.cpp | REFERENCE | нет |
| `tools-structured-outputs` | tools_structured | вызов инструментов + JSON Schema | llama.cpp b10964 `--jinja` + грамматика | REFERENCE на файлы baseline | да |

Пояснения:
- **GPT-OSS-120B = PRELIMINARY LAB_PRIMARY**: единственный 7/7 в короткой проверке A–G (90 с, 49 т/с),
  но FAST/Nex/Occamy тем же набором не прогонялись — это не финальный победитель; production-модель не менялась.
- **Q4_K_M и Q4_K_XL — разные записи**, разные id, разные шаблоны имён; валидатор отвергает
  манифест, если шаблон одного совпадает с файлом другого (тест `test_mixing_q4_entries_is_rejected`).
- **reuse_only**: установленный файл никогда не перекачивается. Если его нет — обычный путь, но
  без хэша это `UNPINNED_REFUSED`.
- Фото/видео **не дублируются**: профили лишь ссылаются на id из `tools/studio_models.json`,
  валидатор проверяет, что id существуют, поверхность совпадает (photo→image, video→video) и у записи
  есть provider/license/status/source. Файлы и sha256 — в медиа-`MANIFEST.json`, проверка —
  `media_bootstrap.py validate`.
- **Инструменты/структурированный вывод**: схему обеспечивает рантайм (llama-server `--jinja` для
  tool_calls, `response_format: json_schema` / GBNF-грамматика), модель выбирает инструмент и аргументы.
  В bake-off задачу F (восстановление после ошибки инструмента) оба кванта Qwen провалили, GPT-OSS прошёл.

## Совместимость рантаймов

| рантайм | статус | условия |
|---|---|---|
| llama.cpp **b10964** Vulkan | версия закреплена (наблюдалась у владельца), sha256 бинаря — нет | `runtime-check` сверяет `llama-server --version` с 10964 |
| форк llama.cpp **qwen4exp** (Flash-Next) | не выбран | по описаниям на GitHub: `lendome/llama.cpp-qwen4exp` (PR #27742, только Ubuntu-бинарники), `chishiki37/qwen38-flash-next-aimax` (Q4_K_XL на Strix Halo, Vulkan), `aic0d3r/qwen38-strix-halo-harness`. Ставить отдельной папкой, **никогда не заменяя** b10964 |
| SnowLLM 0.3.2 | не закреплён | см. ниже |
| GLM-5.3-Flash | не выбран | кандидат `julianmb/halo-glm53flash` (320B-A18B, «9,31 т/с» — заявлено в описании, не измерено). Модель на пределе 128 ГБ: сначала доказать загрузку без разрушительного давления памяти |
| FP8 (vLLM/SGLang ROCm) | не установлен | llama.cpp FP8-safetensors не читает; у gfx1151 (RDNA 3.5), насколько известно, нет аппаратного FP8-матричного пути — ожидать эмуляцию (НЕ ПРОВЕРЕНО); Windows-поддержка не подтверждена |

### SnowLLM — условия ядер (проверить на ПК владельца, ничего не ставя вслепую)
Из метаданных GitHub (22.09): `SnowLLM/SnowLLM`, создан 03.08.2026, 2 звезды, 1 форк, Python, темы
`amd-rocm`, лицензия репозитория **Apache-2.0**. Заявление в описании: до 9× prefill и 2× decode
против llama.cpp на тех же GGUF (не проверено). Содержимое репозитория прочитать не удалось.
Условия:
1. gfx1151 должен быть в списке скомпилированных ядер (не только gfx1100 через `HSA_OVERRIDE_GFX_VERSION=11.0.0`);
2. версия ROCm/HIP, под которую собран, и поддерживает ли она gfx1151 **на Windows** (HIP SDK). Если только Linux ROCm — для этого ПК NOT_SUITABLE;
3. лицензия предсобранных ядер/code objects отдельно от Apache-2.0 репозитория;
4. никаких изменений драйвера GPU, BIOS, системного Python (`model_fetch` их не трогает никогда);
5. выигрыш воспроизвести `bakeoff.py` на тех же промптах до любого использования.

## Лицензии

| профиль | лицензия | принятие |
|---|---|---|
| Qwen3.x (27B, 35B-A3B, Flash-Next, FP8) | НЕ ПРОВЕРЕНО (обычно Apache-2.0 — сверить карточку; `pin` запишет `license.id_from_hf_card`) | нет / сверить |
| GPT-OSS-120B | Apache-2.0 + политика использования OpenAI gpt-oss | нет |
| GLM-5.3-Flash | НЕ ПРОВЕРЕНО | сверить карточку |
| SnowLLM | Apache-2.0 (репозиторий), ядра не проверены | нет |
| llama.cpp | MIT | нет |
| Фото/видео | по `studio_models.json` (Apache-2.0; SDXL — CreativeML OpenRAIL++-M с ограничениями использования) | SDXL: да |

## Команды (на ПК владельца)

Папка моделей: `--models-dir`, иначе `BOSSMAN_MODELS_DIR`, иначе `%LOCALAPPDATA%\Bossman\models`.
Установленные сегодня модели лежат в `%USERPROFILE%\Bossman\models` — укажите её как `--models-dir`
(или как `--reuse-dir`, тогда совпадающие файлы жёстко связываются, без копирования и скачивания).

```powershell
$py = "python"; $mf = "app-support\model_fetch.py"   # или tools\model_fetch.py из checkout
$models = "$env:USERPROFILE\Bossman\models"

# 0. манифест цел?                                      exit 0 / 2
& $py -I $mf validate --json
# 1. план без сети и без хэширования                    exit 0 / 2
& $py -I $mf plan --models-dir $models --json --report plan.json
# 2. проверка установленного (первый раз хэширует ~100 ГБ, дальше из кэша path+size+mtime)
& $py -I $mf verify --models-dir $models --profile baseline-installed --profile qwen3.8-27b-ud-q4_k_m-installed --json --report verify.json
#    сейчас ожидаемо exit 5 (UNVERIFIED: всё UNPINNED, записан локальный TOFU-хэш); 1 — если файл пропал/изменился/префикс не совпал
# 3. закрепить хэши с HF (нужна сеть к huggingface.co)   exit 0 / 1
& $py -I $mf pin --profile qwen3.8-27b-q4_k_xl --out "$models\model_profiles.pinned.json" --json
# 4. скачать закреплённое (докачка, диск, sha256, атомарное переименование)
& $py -I $mf fetch --manifest "$models\model_profiles.pinned.json" --models-dir $models --profile qwen3.8-27b-q4_k_xl --reuse-dir "D:\old-models" --json --report fetch.json
# 5. рантайм
& $py -I $mf runtime-check --profile baseline-installed --runtime-bin "$env:USERPROFILE\Bossman\runtime\llama.cpp\llama-server.exe" --json
```

Остановить скачивание: создать файл `<models>\STOP-model-fetch` (или `--stop-file`) либо Ctrl+C.
`.part` остаётся, следующий `fetch` докачает по HTTP Range (если сервер Range не поддерживает — честно
начнёт с нуля и запишет `range_supported: false`). Пока STOP-файл лежит, `fetch` не стартует (exit 3).

### Коды выхода
| код | вердикт | смысл |
|---|---|---|
| 0 | OK | всё обязательное проверено по sha256 |
| 1 | FAILED | обязательный файл: FAILED_HASH / FAILED_SIZE / FAILED_NETWORK / MISSING |
| 2 | INVALID | манифест или аргументы неверны, неизвестный профиль |
| 3 | REFUSED | не хватает диска (до начала), STOP-файл, или обязательные файлы только UNPINNED_REFUSED |
| 4 | CANCELLED | остановлено STOP/Ctrl+C, `.part` сохранён |
| 5 | UNVERIFIED | ошибок нет, но есть обязательные файлы только с TOFU-хэшем |

Сбой необязательного профиля не меняет код: его файлы получают `SKIPPED_OPTIONAL_FAILURE` с `cause`.

### Состояния файлов
`REUSED` (есть и совпал / связан из `--reuse-dir`), `DOWNLOADED`, `RESUMED`, `FAILED_HASH` (файл **не**
продвинут, плохой `.part` удалён), `FAILED_SIZE`, `FAILED_NETWORK` (`.part` сохранён для докачки),
`CANCELLED`, `NOT_STARTED`, `SKIPPED_OPTIONAL_FAILURE`, `UNPINNED_REFUSED`, `UNVERIFIED_TOFU`,
при `verify` — `VERIFIED` / `MISSING`. `plan`: `PRESENT_VALID`, `PRESENT_SIZE_OK_NOT_HASHED`,
`PRESENT_SIZE_MISMATCH`, `PRESENT_HASH_MISMATCH`, `PRESENT_UNPINNED`, `REUSABLE`, `PARTIAL`, `MISSING`, `UNPINNED_MISSING`.

Проверка диска: до начала `свободно ≥ max(докачать + запас (--margin-bytes, 10 ГиБ), min_free_disk профиля)`;
уже скачанные байты `.part` вычитаются. Необязательный профиль, который не влезает, пропускается
(`cause: DISK_INSUFFICIENT`), обязательные продолжаются. Для UNPINNED размер неизвестен — `plan` пишет это в `fits_note`.

Служебное состояние — `<models>\.model_fetch\` (кэш хэшей, ссылки `refs.json` для файлов с другого
диска, `tofu-lock.json`). Удалять можно: будет лишь повторное хэширование.

## Что осталось владельцу / интегратору
- `model_fetch.py` и `model_profiles.json` пока **не включены** в сборку `app-support\` (сборщик комплекта
  вне зоны этой задачи) — до этого запускать из checkout: `python -I tools\model_fetch.py ...`.
- Выполнить `pin` для нужных профилей на ПК владельца и сверить префикс `322e194f`.
- Выбрать форк для Flash-Next и рантайм для GLM; проверить условия SnowLLM; записать sha256 бинарей.
