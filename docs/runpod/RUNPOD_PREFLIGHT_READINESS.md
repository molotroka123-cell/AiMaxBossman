# BOSSMAN — RUNPOD PREFLIGHT FINAL READINESS PASS

**START_REMOTE_SHA:** `82e5099441b6005549cec72422fd02fb5c320330`
**Ветка:** `claude/bossman-control-v03-43igbk` · без force-push

Задача этого прохода — НЕ добавлять функциональность и не переделывать
архитектуру, а сделать репозиторий готовым к немедленному запуску на
RunPod Linux/GPU. Аудит вели 2 параллельных read-only агента по всему
дереву `bossman-core/` и `command-center/`.

## Метод

1. Fetch/reconcile CURRENT REMOTE HEAD (in sync, без конфликтов).
2. Подтверждена регрессия на восстановленной после рестарта контейнера
   PostgreSQL: core и command-center зелёные (см. ниже).
3. Два аудита: (A) Windows-only пути/шеллы/порты/CUDA-discovery/Docker/
   Postgres-допущения; (B) хардкод в benchmark-инструментах
   (`tools/local_hardware_ab.py`, `bossman-core/tools/measure_v26.py`,
   discovery, Gateway launcher, router).
4. Добавлен `tools/runpod_preflight.py` (не существовал).
5. Исправлены только ВОСПРОИЗВЕДЁННЫЕ баги (не гипотетические улучшения).

## RunPod preflight script

`tools/runpod_preflight.py` — read-only, не скачивает модель, не трогает
сеть кроме локальных TCP-проб (Postgres, Ollama). Печатает ровно тот набор
полей, что требует раздел 5 задачи, плюс явный `BLOCKERS:` список и код
возврата 0/1. На этом (безGPU) хосте честно печатает `RUNPOD_READY=NO` с
причиной `no GPU visible via nvidia-smi` — не выдаёт fake-green.

```
$ python tools/runpod_preflight.py
LOCAL_SHA=82e5099441b6005549cec72422fd02fb5c320330
OS=Linux PRETTY_NAME="Ubuntu 24.04.4 LTS"
GPU=NONE
VRAM=N/A
CUDA=nvcc:absent
PYTHON=3.11.15
RAM=15.7GB
DISK=17.1GB free / 252.0GB total
PERSISTENT_WORKSPACE=NO
MODEL_RUNTIME=ollama binary=NO, reachable=NO (http://127.0.0.1:11434)
MODEL_CACHE=hf=False, ollama=False
POSTGRES=reachable=YES (127.0.0.1:5433)
GATEWAY=importable
CLOUD_KEYS_PRESENT=none
RUNPOD_READY=NO
BLOCKERS:
  - no GPU visible via nvidia-smi
```

На реальном RunPod-поде с GPU и Linux этот же скрипт должен вернуть
`RUNPOD_READY=YES` (при условии `bossman.gateway.config` импортируется —
уже подтверждено здесь) — заполнять `docs/runpod/RUNPOD_HARDWARE_INVENTORY.md`
им же, одной командой (`--json`).

## Аудит A — Windows/Linux блокеры (полный отчёт агента, суммарно)

Вывод агента: кодовая база **необычно хорошо защищена** — почти весь
Windows-специфичный код лежит за `platform.system()`/`os.name` проверками,
опасные импорты (`pywinauto`, `pyautogui`) ленивые и try/except. Найден
**один реальный P1** и несколько документированных P1-заметок для
деплоя (не баги — дефолты docker-compose, ожидаемо переопределяемые env).

| # | Находка | Класс |
|---|---|---|
| **P1 — ИСПРАВЛЕНО** | `bossman/toolkit/browser.py:_headless()` — по умолчанию headed Chromium; на безголовом RunPod-контейнере (нет `$DISPLAY`) Playwright не запустится вовсе | Реальный блокер |
| P1 (deployment note, не баг) | `config.py` дефолты `postgres:5432`/`redis:6379`/`litellm:4000`/`llama-swap:8080` — docker-compose имена сервисов, не резолвятся на голом RunPod без env; **fail LOUD с понятной ошибкой**, не тихо | Задокументировано в `.env.example`, требует `source .env` на RunPod |
| P1 (by design, не баг) | `CORE_HOST`/`bind_host`/`BCC_HOST` дефолт `127.0.0.1` — сознательный security-дефолт («наружу — только через Tailscale»); на RunPod без Tailscale сервисы недоступны снаружи, пока не выставлен `0.0.0.0` | Intentional |
| P2 (feature gap, не баг) | `resource_brain/probe.py` — нет NVIDIA/CUDA-пробы вообще, только AMD unified sysfs + CPU fallback; на RunPod GPU останется недоучтён этим модулем (`command-center/bcc/metrics.py` уже умеет `nvidia-smi` — `resource_brain` не умеет) | Не чинил: это добавление возможности, а не воспроизведённый баг — вне рамок "DO NOT add features" |
| P2 | `computer_operator/applist.py` — allowlist приложений только Windows-путей; на Linux `AppLaunchAdapter` честно падает `RuntimeError`, не тихо | Feature gap, не портируемость |
| Не блокер | `shell=True` — не найден нигде в реальном коде (только в тестах/regex-сигнатурах, которые его ДЕТЕКТЯТ) | — |
| Не блокер | `SANDBOX_MODE` docker/local/unknown fail-closed — идентично на Linux, volume-mount через `Path.resolve()`, без Windows-путей | — |
| Не блокер | Python `>=3.11` консистентно в обоих `pyproject.toml`, CI матрицей гоняет 3.11/3.12 на `ubuntu-latest` | — |

## Аудит B — benchmark-харнессы (полный отчёт агента, суммарно)

| Файл | Было | Исправлено |
|---|---|---|
| `tools/local_hardware_ab.py` | `MODEL` — bare-константа без override | `BOSSMAN_AB_MODEL` env override |
| `tools/local_hardware_ab.py` | "direct"-рука хардкодила `127.0.0.1:11435` (Windows/WSL2 workaround-порт) независимо от `OLLAMA_HOST`, пока "bossman"-рука честно читала `OLLAMA_HOST` — **на RunPod два плеча A/B тихо ходили бы к РАЗНЫМ Ollama** | Единый резолвер `_ollama_direct_base_url()`: `BOSSMAN_AB_OLLAMA_URL` > `OLLAMA_HOST` > `11434` на Linux (workaround `11435` остаётся дефолтом только на `win32`). `write_config()` использует тот же резолвер — оба плеча гарантированно смотрят в один Ollama |
| `tools/local_hardware_ab.py` | RSS-сэмплер матчил только `"ollama.exe"` → `peak_ollama_rss` всегда 0 на любом Linux, включая RunPod | Матчит `{"ollama.exe", "ollama"}` |
| `bossman-core/tools/measure_v26.py` | Нет GPU/VRAM-измерений вообще (не деградация — просто не реализовано) | Не менял: это добавление функциональности, вне рамок задачи (задокументировано как честный gap) |
| `bossman/gateway/config.py`, `config/gateway.example.yaml` | Уже полностью config/env-driven | Без изменений — не требовалось |
| `command-center/bcc/discovery.py`, `model_router.py`, `openrouter.py` | Порты/хосты — переопределяемые дефолты проб, `BCC_MODELS_DIRS` уже `os.name`-aware | Без изменений — не требовалось |
| `gateway/backends.py`, `gateway/app.py`, `gateway/main.py` | Нет процесс-спавна с Windows-путями; bind host/port — из конфига | Без изменений — не требовалось |

## Исправленные баги (REPRO → ROOT CAUSE → FIX → TEST → REGRESSION)

### 1. Headed Chromium по умолчанию на headless-хосте
- **REPRO**: `BOSSMAN_BROWSER_HEADLESS` не задан, `DISPLAY` отсутствует (обычное состояние RunPod-контейнера) → `_headless()` возвращала `False`.
- **ROOT CAUSE**: дефолт `"0"` жёстко подразумевал «есть экран», без проверки POSIX/`$DISPLAY`.
- **MINIMAL FIX**: `bossman-core/bossman/toolkit/browser.py:_headless()` — явный env всегда побеждает; иначе на POSIX без `$DISPLAY` → `True`; на Windows (нет концепции `$DISPLAY`) поведение не меняется.
- **TEST**: `bossman-core/tests/test_browser_headless_runpod.py` (4 теста).
- **REGRESSION**: `test_browser_policy.py` + `test_browser_support_helper.py` зелёные (14 всего).

### 2. A/B-харнесс: два плеча ходят к разным Ollama на Linux
- **REPRO**: на чистом Linux/RunPod (`OLLAMA_HOST` не задан) "bossman"-плечо резолвит `127.0.0.1:11434` (реальный дефолт Ollama), "direct"-плечо — жёстко `127.0.0.1:11435` (Windows/WSL2-костыль) → сравниваются два разных сервера, если оба порта чем-то заняты, либо direct-плечо просто ничего не находит.
- **ROOT CAUSE**: резолюция `OLLAMA_HOST` была реализована дважды и независимо — один раз честно (`write_config`), один раз хардкодом (`run_arm` вызовы).
- **MINIMAL FIX**: единая `_ollama_direct_base_url()`, используемая обоими путями; `MODEL` тоже сделан переопределяемым (`BOSSMAN_AB_MODEL`).
- **TEST**: `tests/test_local_hardware_ab_portability.py` (7 тестов, репо-корень).
- **REGRESSION**: скрипт синтаксически валиден (`py_compile`), резолвер проверен на этом Linux-хосте (даёт `11434`, не `11435`).

### 3. RSS-сэмплер не видит Ollama-процесс на Linux
- **REPRO**: `psutil.process_iter` матчит только `"ollama.exe"` → на Linux `peak_ollama_rss` всегда 0, без ошибки (тихая потеря метрики).
- **ROOT CAUSE**: то же самое — метрика писалась с оглядкой только на Windows.
- **MINIMAL FIX**: матч по множеству `{"ollama.exe", "ollama"}`.
- **TEST**: включён в `test_local_hardware_ab_portability.py`.

### 4. Отсутствовал RunPod preflight script
- Добавлен `tools/runpod_preflight.py` + `tests/test_runpod_preflight.py` (8 тестов).

## Локальная политика (раздел 6 задачи)

Подтверждено (не менялось — уже было доказано в предыдущих проходах и
осталось верным): `cloud_policy=never` → `CloudDenied` жёстко до сети
(`bossman/llm.py:107-109`); ни один трекаемый файл не содержит реального
секрета (grep по паттернам API-ключей — 0 совпадений вне test/redact-кода);
только `.env.example`-шаблоны в git, реальных `.env` нет.

## Регрессия (финальный HEAD)

```
bossman-core (живой PostgreSQL 16.13):  1274 passed, 5 skipped, 0 failed
command-center:                          634 passed, 2 skipped, 0 failed
tests/ (repo root — CI/RunPod tooling):  15 passed, 0 failed
compileall: PASS
```
(1274 = прежние 1270 + 4 новых `test_browser_headless_runpod.py`.
15 в корне = 8 `test_runpod_preflight.py` + 7 `test_local_hardware_ab_portability.py`.)

## Итог

| Поле | Значение |
|---|---|
| P0 | 0 |
| P1 | 1 найден, 1 исправлен (headless default) — плюс 2 задокументированных deployment-заметки (не баги, ожидаемое поведение с известным обходом) |
| P2 | 2 (resource_brain без NVIDIA-пробы — сознательно не чинил, вне рамок «не добавлять функциональность»; applist только-Windows — feature gap, не портируемость) |
| RUNPOD_BLOCKERS | Нет кода, блокирующего Linux/RunPod. Единственный реальный блокер (headless) исправлен. Остаётся: реальный GPU не может быть протестирован без физического RunPod-пода (см. `RUNPOD_HARDWARE_INVENTORY.md` — шаблон, готов к заполнению одной командой). |

**BOSSMAN RUNPOD PREFLIGHT READY**
