# Epoch 6 — Architecture & Workstreams

## Performance architecture model

Epoch 6 рассматривает latency как сумму независимых слоёв:

`startup/readiness → UI/API → model/runtime → observe/plan/verify → persistence → media/background contention`.

Каждый слой должен измеряться отдельно, иначе «ускорение» одного компонента может просто переместить задержку в другой.

## Workstream A — Startup / readiness

- timestamp config, identity/vault, DB, API bind, UI static ready, model ready, computer-use ready, media ready, Fleet ready;
- разделить `CORE_READY`, `UI_READY`, `MODEL_READY`, `COMPUTER_USE_READY`, `MEDIA_READY`, `FLEET_READY`;
- optional subsystems инициализировать lazy только после профилирования;
- не разрешать first action до readiness policy/identity/DB truth path.

## Workstream B — Command Center

- инвентаризация `setInterval`, recursive polling, broad refresh и больших responses;
- adaptive polling для active/idle/hidden states;
- row/delta updates вместо full page rebuild там, где профилирование подтверждает выигрыш;
- логи, большие списки и telemetry не держать в synchronous render path;
- Stop/Pause/approval остаются immediate path, не зависят от polling cadence.

## Workstream C — Backend/API

- проверить per-request HTTP clients вне уже reusable GatewayClient;
- найти blocking file/DB/subprocess/DNS work в async handlers;
- top-20 query profile по cumulative time и p95;
- server-side pagination/filtering с честными `total/returned/has_more`;
- authoritative journal/effect/evidence commits не переводить в lossy queues.

## Workstream D — Computer Use

Профилировать по фазам:

`observe → context/summarize → model/plan → policy → approval → dispatch → post-observe → verify → persist`.

Оптимизации:

- reuse уже verified post-action observation там, где freshness contract разрешает;
- UIA/structure-first probe для задач без visual semantics;
- full screenshot только когда нужен pixel state;
- compact planner/tool schema;
- не вызывать модель для deterministic state transitions/reconciliation;
- 401/404/parse errors должны быть видимы и не тратить replan budget как «непонятная ошибка».

## Workstream E — Local Model / runtime

- измерить model load ms, reload count, resident allocation, KV/context, TTFT, tokens/s, queue time;
- A/B `keep_alive`, max loaded models, parallelism, context, Flash Attention, KV cache type/quantization;
- residency-aware routing после measurement;
- одинаковая модель/config между baseline и orchestration candidate.

## Workstream F — Video Studio

Разделить resource classes:

- interactive preview;
- thumbnail/waveform/proxy;
- analysis/transcription;
- batch export.

Owner interaction и preview получают более высокий priority, чем batch export. Concurrency cap определяется измерениями CPU/GPU/disk, а не догмой `=1`.

## Workstream G — Persistence & telemetry

- authoritative: intent, receipt, evidence, owner control, budgets — durable/fail-closed;
- observational: perf counters/UI analytics — допускают batching при видимой потере;
- отдельно измерить write amplification на растущих journals/history.

## Workstream H — Skills/context

- 1–2 релевантных skill docs, не вся библиотека;
- cache immutable tool schemas/skill metadata;
- summary старого контекста с provenance;
- policy/evidence/owner constraints всегда остаются в prompt/runtime path;
- benchmark prompt tokens + TTFT + held-out verified success.

## Workstream I — Unified resource scheduler (post-freeze architecture)

После low-risk phase:

- единый admission с CPU/RAM/GPU/unified memory/media/model envelopes;
- interactive owner controls и verification priority выше batch jobs;
- предотвращение paging/oversubscription;
- model residency cost учитывается при routing;
- missed event recovery через periodic reconciliation.

## Workstream J — Design refresh (воспринимаемая отзывчивость)

Полное описание — `DESIGN_REFRESH_WORKSTREAM.md`. Здесь только место в системе.

Workstream J — единственный, который оптимизирует не машину, а восприятие. Он
нужен потому, что интерфейс без honest-состояний ощущается медленным и
непредсказуемым даже после того, как backend ускорен: молчащий экран
неотличим от зависшего, а невидимый Stop равносилен отсутствующему.

Зависимости:

- от **Workstream B** (Command Center rendering/polling) — J не имеет права
  ухудшить frame/interaction latency; метрики берутся оттуда же;
- от **Workstream D** (Computer Use) — прогресс и owner control рисуются по
  реальным состояниям задачи, а не по таймеру;
- от **Workstream F** (Video Studio) — анимации и превью не конкурируют за
  ресурсы с экспортом.

Жёсткое ограничение: J не меняет фреймворк, не добавляет возможностей и не
ослабляет ни один существующий UX/owner-control тест. Приоритет внутри J —
сначала честность состояний и видимость контроля, только потом визуальная
консистентность.

## Already optimized / do not duplicate blindly

- GatewayClient уже использует reusable `httpx.AsyncClient` на рассмотренной линии;
- Computer Operator уже получил safe observation reuse;
- Windows `/v1`, observer deps, schema/token/Unicode issues уже были найдены live-run;
- Video Studio уже имеет FFmpeg/browser acceptance harness.

Каждый новый perf patch должен доказать, что он устраняет **текущий** measured hot path, а не старую запись из аудита.