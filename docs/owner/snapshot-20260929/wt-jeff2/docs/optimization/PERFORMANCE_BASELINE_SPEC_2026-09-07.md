# AiMaxBossman — Performance Baseline & Resource Measurement Spec

**Статус:** NOT_RUN  
**Назначение:** получить фактические цифры до Optimization Wave и перестать путать прогнозы разных аудитов с реальными измерениями.

## 1. Почему baseline обязателен

Сейчас в независимых аудитах встречаются числа вроде `3–4 GB idle RAM`, `15–20% idle CPU`, `8–12 s cold start`, но для текущего финального Bossman они не имеют единого exact-SHA owner baseline. Поэтому до начала оптимизаций любые проценты ускорения — гипотезы.

## 2. Reference environments

### Owner acceptance Windows machine
Записывать фактическую конфигурацию из текущего Windows owner run и не смешивать её с будущим железом.

### Future target — Ryzen AI Max+ 395 / 128 GB unified memory
На этой машине RAM и GPU shared memory нельзя складывать как две независимые ёмкости. Нужен общий accounting committed/unified memory плюс доступная dedicated/shared GPU telemetry.

### CI Linux
Использовать только для regression и сравнительных микро-бенчмарков. Не выдавать Linux GitHub runner за пользовательскую Windows производительность.

## 3. Process-tree RAM accounting

Снимать минимум каждые 500 ms и отдельно фиксировать high-water mark:

- `bossman-core`;
- Gateway;
- Command Center;
- PostgreSQL/container memory attributable to Bossman session;
- Redis если включён;
- Chromium/Playwright/browser helper processes;
- UIA/Windows helper processes;
- FFmpeg/ffprobe workers;
- Fleet/local workers;
- Ollama/model runtime.

Записывать:

- process PID/name/role;
- RSS/working set;
- private bytes/private working set where OS exposes it;
- shared memory where meaningful;
- process CPU;
- start/end timestamps.

Do not sum shared pages as if they were private allocations.

## 4. VRAM / unified memory accounting

### Discrete GPU
Если есть выделенная VRAM:

- total dedicated VRAM;
- used dedicated VRAM before Bossman;
- used dedicated VRAM after model load;
- per-process GPU allocation if driver API exposes it;
- encoder/decoder allocation during Video Studio;
- peak and post-task residual.

### Unified-memory APU
Записывать отдельно:

- total system memory;
- OS committed bytes;
- runtime/model allocation;
- driver-reported dedicated reservation if any;
- shared GPU allocation if observable;
- pagefile/swap activity;
- memory bandwidth/utilization where available.

**Rule:** `model memory` не считается дополнительной к RAM ёмкостью, если она физически живёт в unified memory.

## 5. Scenarios

### S0 — machine idle
5 minutes before Bossman startup.

### S1 — Bossman UI idle
Core/Gateway/CC up, no browser task, no local model loaded, 5 minutes.

### S2 — local model warm
Primary model loaded, one chat completed, 5 minutes idle.

### S3 — Command Center navigation
Open all primary pages, switch 20 times, no active jobs.

### S4 — Computer Use
A fixed safe 5-step mission with real observation/planning/verification.

### S5 — Video preview
Import short fixture → trim → preview → playback.

### S6 — Video export contention
Export reference clip while navigating UI and making local API calls.

### S7 — Web Studio
Edit/save/reopen project.

### S8 — concurrency
3–5 safe tasks including one model task and one media/light I/O task.

### S9 — restart/resume
Controlled restart during safe/read-only mission and recovery timing.

### S10 — model residency
30-min mixed workload that would tempt model unload/reload/switch.

## 6. Startup timing

Required markers:

- `t_process_start`
- `t_config_ready`
- `t_db_ready`
- `t_policy_identity_ready`
- `t_api_bound`
- `t_ui_first_response`
- `t_ui_interactive`
- `t_model_ready`
- `t_computer_use_ready`
- `t_media_ready`

Derived:

- `cold_start_to_ui_ready_ms`
- `warm_start_to_ui_ready_ms`
- `ui_to_model_ready_ms`
- `ui_to_computer_use_ready_ms`

## 7. UI/API latency

Collect at least 100 local non-model interactions:

- page navigation;
- task list;
- task detail;
- approvals;
- provider/model catalog local reads;
- Video Studio status/project reads;
- Web Studio project reads.

Report p50/p95/p99 and worst. A lower average with worse p95 is not an automatic win.

## 8. Computer Use metrics

Per step:

- observe ms;
- probe/summarization ms;
- planner/model ms;
- policy ms;
- dispatch ms;
- post-observe ms;
- verification ms;
- persistence ms;
- total step ms;
- observation count;
- model call count;
- replan count;
- owner intervention count.

Report `verified_actions/min` and `observations/verified_action`.

## 9. Model metrics

For each model/config:

- load time;
- TTFT;
- prompt tokens/s if available;
- generation tokens/s;
- context length actually used;
- quantization;
- resident memory;
- KV cache memory if runtime reports it;
- queue delay;
- unload/reload count;
- OOM/paging.

Do not compare different models and call it a Bossman framework speedup.

## 10. Video metrics

- import ms;
- analysis/proxy/waveform ms;
- preview queue wait;
- preview render ms;
- output verification ms;
- browser metadata load ms;
- click → first playable frame;
- export runtime;
- UI local API p95 while export runs;
- CPU/GPU/disk high-water mark;
- owner Stop/Pause latency.

## 11. Output schema

Each run should write a machine-readable JSON artifact:

```json
{
  "schema_version": 1,
  "source_sha": "...",
  "tree_sha": "...",
  "environment": {},
  "scenario": "S6_VIDEO_EXPORT_CONTENTION",
  "samples": [],
  "summary": {
    "p50_ms": 0,
    "p95_ms": 0,
    "ram_hwm_mb": 0,
    "vram_or_unified_hwm_mb": null
  },
  "claims": {
    "owner_windows": false,
    "live_model": false,
    "synthetic": false
  }
}
```

## 12. Acceptance discipline

- минимум 5 повторов cold/warm scenarios;
- один и тот же source SHA для before/after session groups или явно указанный base/candidate;
- одинаковая модель/quant/context для framework comparison;
- no cherry-picking only fast runs;
- failures остаются в dataset;
- performance telemetry не меняет mission truth;
- profiler overhead измерить отдельно;
- raw credentials/private prompts не сохранять.

## 13. Что мы сможем ответить после baseline

Только после этого можно честно сказать:

- сколько RAM занимает Bossman без модели;
- сколько добавляет браузер/UIA/Video;
- сколько памяти занимает модель и KV cache;
- сколько dedicated VRAM реально используется;
- насколько быстрее cold/warm startup;
- что именно вызывает UI jank;
- есть ли смысл держать одну большую модель resident;
- какой concurrency безопасен для целевой машины.

**Current RAM/VRAM answer: UNKNOWN / NOT YET MEASURED ON FINAL SHA.**