# Локальный медиа-движок: stable-diffusion.cpp (Vulkan) в Studio

Статус: **WIP / UNVERIFIED**. Ни один пресет не объявлен рабочим. Единственный живой прогон
(2026-09-21, Radeon 8060S): 832x480 / 17 кадров / 20 шагов / без флагов → один правильный ролик;
480x288 / 10 шагов (с `--diffusion-fa` и без) → цветной шум. Причина **не изолирована**.
Все тесты в репозитории гоняют MOCK_ENGINE (Python-скрипт, пишущий testsrc через ffmpeg) и
не являются доказательством генерации.

Код: `command-center/bcc/studio/providers/sdcpp.py`.
Тесты: `command-center/tests/test_studio_sdcpp_provider.py` (продуктовый путь через очередь),
`command-center/tests/test_studio_sdcpp_hostile.py` (red-team), `tests/test_media_bootstrap.py`.
Инструменты: `tools/media_bootstrap.py`, `tools/media_ab_preset.py`.

## 1. Конфигурация (переживает запуск через ярлык / Start-Bossman.cmd / новый shell)

Приоритет по каждой переменной отдельно: переменная окружения → файл конфигурации.

| Источник | Что задаёт |
|---|---|
| `BOSSMAN_SDCPP_BIN` | путь к `sd-cli(.exe)` |
| `BOSSMAN_MEDIA_MODELS` | каталог с `MANIFEST.json` и файлами моделей |
| `<data_dir>/media/config.json` | `{"schema_version":1, "sdcpp_bin": "...", "models_dir": "...", "written_at": "..."}` |

`data_dir` — семантика `bcc.config._data_dir()`: `BCC_DATA_DIR`, иначе платформенный каталог
данных (`%LOCALAPPDATA%\Bossman\CommandCenter` на Windows). Файл пишет
`python tools/media_bootstrap.py configure --sdcpp-bin <sd-cli> --models-dir <dir> [--data-dir <dir>]`
(проверяет, что бинарь существует и манифест валиден, запись атомарная через `.tmp` + rename).

Отсутствующий или битый `config.json`, отсутствующий бинарь, невалидный манифест — **никогда не
ломают старт приложения**: `configuration()` возвращает `None`, модели показываются как
`configured: false`. Причину даёт `describe_configuration()["error"]`.

## 2. Схема `MANIFEST.json` (schema_version 1)

```json
{
  "schema_version": 1,
  "engine": {
    "release": "master-890-74988b2",
    "binary_sha256": "<64 hex, необязательно; если есть — бинарь сверяется>"
  },
  "engines": {
    "wan2.2-ti2v-5b": {
      "files": {
        "diffusion":    {"path": "wan2.2-ti2v-5b/wan2.2_ti2v_5B_Q8_0.gguf", "bytes": 123, "sha256": "<64 hex>",
                         "revision": "57437632", "url": "https://huggingface.co/..."},
        "vae":          {"path": "...", "bytes": 0, "sha256": "..."},
        "text_encoder": {"path": "...", "bytes": 0, "sha256": "..."}
      }
    },
    "z-image-turbo": {"files": {"diffusion": {...}, "vae": {...}, "text_encoder": {...}}}
  }
}
```

Правила:
- `path` — относительный, только `/`, без `..`, без буквы диска и абсолютных путей. Файл должен
  быть обычным файлом внутри каталога моделей: **симлинки и junction отвергаются** (и сам файл, и
  любой каталог на пути — `resolve()` обязан совпасть с лексическим путём).
- `bytes` — целое, `sha256` — 64 hex в нижнем регистре; `revision`, `url` — необязательные строки.
- Обязательные роли для обоих движков: `diffusion`, `vae`, `text_encoder`. Все файлы записи
  хэшируются (проектор/энкодер/VAE — всё, что перечислено).
- `revision` **только декларируется** (в provenance попадает как `revision_declared`); по байтам
  ревизию проверить нельзя — проверяется sha256.

Тот же валидатор продублирован в `tools/media_bootstrap.py` (stdlib), тест
`test_manifest_schema_agrees_with_provider` следит, чтобы они не разошлись.

## 3. Проверка файлов моделей: что реально измеряется

`verify_engine_files(cfg, model_id, mode=...)` считает **настоящий sha256**; в provenance
`sha256_expected` (из манифеста) и `sha256_observed` (посчитанный) — отдельные поля.

Кэш `<data_dir>/studio/engine-work/model-hash-cache.json`: ключ = путь + `size, mtime_ns,
ctime_ns, inode` + ожидаемый sha; значение = наблюдённый sha + sample-дайджест. Записывается только
совпавшее наблюдение. Файл несёт `installation_token`; записи с чужим/отсутствующим токеном не
используются (кэш, написанный руками в старом формате, не пропустит первое хэширование).

| mode | когда | политика |
|---|---|---|
| `health` | `runtime.models()` → `engine_files()` | кэш при совпадении stat-ключей и ожидаемого sha; иначе полный sha256. Первое наблюдение всегда хэширует (тест: счётчик вызовов `_sha256` не растёт на повторных health-вызовах). |
| `submit` | перед каждым запуском движка | файлы `< SMALL_FILE_FULL_HASH_BYTES` (64 MiB) — **всегда полный** sha256; крупнее — stat-ключи **и** sample-дайджест (окна по 4 MiB: начало/середина/конец), записанный при последнем полном хэше. |
| `force` | `verify_engine_files(mode="force")`, `media_ab_preset --run`, `media_bootstrap validate` | полный sha256 всего, всегда. |

Честная граница sample-политики: порча того же размера **вне** трёх окон на файле ≥ 64 MiB при
неизменных stat-ключах на submit не ловится — ловится `force`. На Linux/NTFS перезапись меняет
`ctime_ns` (его нельзя вернуть `utime`), поэтому кэш промахивается и файл хэшируется целиком; тест
`test_large_file_submit_policy_sampled_and_force_full` моделирует ФС без надёжного ctime и
проверяет обе стороны (ловит в окне, не ловит вне окна, `force` ловит).

Бинарь движка: если в манифесте есть `engine.binary_sha256`, несовпадение = `configured: false`;
без пина — наблюдённый sha записывается в provenance с `match: null`.

## 4. Отмена, таймауты, зомби

- Отмена может прийти **до** появления процесса (задача `_run` ещё не стартовала или внутри
  `_spawn`). Флаг `canceled` проверяется перед spawn и сразу после: отменённая задача **никогда не
  запускает инференс** (тест считает вызовы `create_subprocess_exec`).
- Убийство — всего дерева процессов (psutil children recursive), ожидание reap с таймаутом
  `KILL_WAIT_S`; корень трогается только при совпадении `create_time`.
- stdout читается кусками, строка обрезается до `MAX_LOG_LINE` (4096), хвост — `MAX_LOG_LINES` (60):
  строка в 6 MiB без `\n` не вешает и не роняет worker.
- Жёсткий таймаут провайдера = `deadline_seconds` каталога + 60 с → `failed/timeout`, процесс убит.
- Провал spawn (нет бинаря, нет прав) → `failed/provider_down` с `failure_detail`, не исключение и
  не HTTP 500.
- Ненулевой exit → `failed/malformed`. Нулевой exit **не равен** completed: сырой вывод проходит
  `runtime.verify_file` (ffprobe + полный декод / проверка PNG); битый контейнер → `failed/malformed`.

## 5. Sidecar и reconciliation после рестарта

На каждую задачу в `<data_dir>/studio/engine-work/<rid>.job.json` пишется (эксклюзивно, `O_EXCL` —
повторный submit того же rid невозможен): `pid, create_time, argv, exe, raw, init, started,
studio_job_id, model, owner_pid, owner_create_time`. Файл удаляется после импорта/отмены/провала.

`reconcile_orphans(work_dir, kill=True)`:
1. sidecar, чей владелец (`owner_pid` + `owner_create_time`) жив — пропускается (`skipped_live_owner`):
   это живая задача этого же процесса (два worker'а) или другой живой экземпляр;
2. иначе процесс `pid` убивается **только** если совпал `create_time` (±1 с) **и** argv или exe;
   несовпадение → `pid_reused`, процесс не трогается;
3. `raw/init/*.part` (только внутри work dir) и sidecar удаляются; битый JSON → `errors`, удалён.

Вызывается из конструктора провайдера (дёшево: только `*.job.json`) и из `runtime.setup()`
(один вызов в `try/except`, после пометки `interrupted_unknown`).

## 6. Вход / выход

- `start` — единственная допустимая роль; ровно одно изображение; только для видео-модели.
- data URI: только `;base64`, лимит 15 MiB (как `dispatch.inputs_for`), декод Pillow (`verify()` +
  `load()`), форматы PNG/JPEG, ≤ 32 Мпикс. Мусор с PNG-заголовком отвергается. Отклонённый вход не
  оставляет файлов.
- Проверка места: `shutil.disk_usage(work).free ≥ min_free_bytes` (2 GiB видео / 512 MiB картинка)
  **до** запуска → иначе `failed/provider_down (disk ...)`, движок не стартует.
- Пути: `raw = <work>/<rid>.webm|png`, `init = <work>/<rid>-start.png|jpg` (имя только из uuid);
  `dest` обязан лежать под storage root.
- Финальный файл: transcode/copy в `.<name>.<rid>.part` → `verify_file` → `os.replace`. Любая
  ошибка/отмена удаляет `.part`. Нет ffmpeg/энкодера → `failed/provider_down ("ffmpeg ...")`.

## 7. Provenance (`settings_resolved.engine_trace`)

```
engine, engine_release_declared,
backend: {declared: "vulkan", observed: <строка лога|null>, evidence: "engine_log"|"none", status: "OBSERVED"|"UNVERIFIED"},
generation: {source: "engine_process", engine_binary{sha256_expected, sha256_observed, match, method},
             model_files{role: {name, bytes, sha256_expected, sha256_observed, revision_declared, verification{method, mode}}},
             argv (без абсолютных путей), returncode, elapsed_s, peak_rss_bytes,
             raw_output{name, sha256, bytes, container, width, height},
             frames, fps, duration_s_declared (= frames/fps), duration_s_observed (ffprobe сырого вывода),
             image_to_video, log_tail},
transcode: {tool: "ffmpeg"|"copy", argv, input_sha256 (= raw sha), output_sha256, output_bytes},
import:    {dest_name, sha256 (= transcode.output_sha256), bytes, mime, atomic: true}
```

Backend **не константа**: берётся из лога движка (`ggml_vulkan: ...`, `Vulkan device`), иначе
`UNVERIFIED`. MOCK_ENGINE ничего такого не печатает — в тестах статус `UNVERIFIED`.

## 8. Пресеты и длительность

Дефолт каталога `frames=49, fps=16` → 3.06 с (тест требует ≥ 3 с). Пресет из handoff (17 кадров @16)
— это ~1.06 с, а не «≥3 с». Оба «шумных» варианта из handoff (480x288, 10 шагов) **вне каталога**
(`width ≥ 640`, `steps ≥ 16`) — через Studio их нельзя отправить; план A/B помечает это
`catalog_valid: false`.

`tools/media_ab_preset.py plan --out plan.json [--seed 7]` — 5 вариантов (baseline 832x480/17/20 без
флагов + ровно одна переменная: resolution, steps, `--vae-tiling`, `--diffusion-fa`), один seed,
argv строится продуктовым `sdcpp._argv`. `--run` выполняется **только** при настроенном движке и
`force`-верификации файлов; пишет exit code, время, sha256 вывода, ffprobe — **без оценок качества**.
Сегодня ничего не запускалось.

## 9. `tools/media_bootstrap.py`

```
validate <dir> [--json] [--out report.json]   verdict OK|INVALID; files[{path,status}]: PRESENT_OK | PRESENT_BAD_HASH | MISSING | SIZE_MISMATCH
plan-download <dir>             только missing/bad с url из манифеста; сети нет; NO_URL если ссылки нет
download <dir> --allow-download  явный opt-in; .part → размер → sha256 → rename; провал удаляет .part
write-manifest <dir> --release R [--bin X] [--file ENGINE:ROLE=rel/path ...] [--auto] [--merge] [--out] [--force]
configure --sdcpp-bin X --models-dir D [--data-dir]
```
`--auto`: подкаталог = движок, роль угадывается по имени (`vae`, `ae.`, `umt5/t5/qwen/llm` →
`text_encoder`, иначе `diffusion`) и помечается в `review_needed` — владелец проверяет глазами.
`--merge` сохраняет `url`/`revision` и роли известных путей из старого манифеста. Каталог — аргумент
или `BOSSMAN_MEDIA_MODELS`; путей владельца в коде нет. Хэширование ~25 ГБ один раз — ожидаемо.

## 10. Что НЕ сделано / не доказано

- Реальная генерация через продуктовый путь, I2V от результата Z-Image, отмена живого движка —
  NOT RUN на этой машине (нет бинаря и весов).
- Причина шума не изолирована; пресет не выбран.
- `revision` не проверяется по байтам (только sha256 файла).
- sample-политика для файлов ≥ 64 MiB ловит порчу только в окнах (см. §3).
- Кэш хэшей — оптимизация с токеном установки, не криптографическая защита от подмены на диске.
