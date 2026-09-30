# Epoch 6 — Metrics, Baseline & Benchmark Contract

## Rule zero

Никакой performance claim без frozen baseline на exact source SHA/tree.

## Baseline scenarios

Минимум 5 повторов, median + p95 + worst:

1. cold launch → first usable Command Center;
2. warm launch;
3. idle 5 minutes;
4. normal chat → first useful streamed content;
5. safe 5-step Computer Use mission;
6. navigation по primary Command Center pages;
7. Video preview;
8. long Video export + параллельные UI actions;
9. Web Studio edit/save/reopen;
10. process restart/recovery;
11. 3–5 concurrent safe tasks;
12. model switch + repeated requests.

## Mandatory metadata

`source_sha`, `tree_sha`, timestamp, OS/build, Python, browser, FFmpeg, DB mode, model/provider, quantization, context, hardware, warm/cold flag, process tree, resident model state, scenario id, evidence class.

## Core metrics

| Metric | Meaning |
|---|---|
| `cold_start_to_ui_ready_ms` | process start → usable owner UI |
| `warm_start_to_ui_ready_ms` | warm restart → usable owner UI |
| `ui_interaction_p50/p95_ms` | local owner interactions |
| `local_api_p50/p95_ms` | non-model local APIs |
| `ttfr_ms` | submit → first useful model content |
| `verified_action_p50/p95_ms` | full safe action cycle |
| `verified_actions_per_minute` | verified throughput |
| `observations_per_verified_action` | expensive observations efficiency |
| `idle_cpu_median_pct` | 5-min idle CPU |
| `bossman_process_tree_ram_hwm_mb` | Bossman non-model process-tree HWM |
| `model_resident_memory_mb` | runtime/model allocation |
| `shared_or_unified_gpu_memory_mb` | shared/unified allocation where exposed |
| `dedicated_vram_hwm_mb` | dedicated GPU memory where meaningful |
| `model_load_count/reload_count` | residency churn |
| `preview_click_to_playable_ms` | Video preview responsiveness |
| `interactive_api_p95_during_export_ms` | UI survival under media load |
| `recovery_to_safe_state_ms` | restart/crash recovery latency |

## Target thresholds

Targets are relative to measured baseline:

- cold start: `>=30%` faster;
- warm start: `>=25%` faster;
- TTFR: `>=20%` faster on same model/config;
- Computer Use verified p50: `>=20%` faster with unchanged safety tests;
- background UI/network work: `>=30%` reduction when freshness remains acceptable;
- avoidable reloads: `0` in 30-min scripted workload;
- during export local interactive API p95: `<2x` idle p95;
- recovery: no regression >10%;
- RAM HWM: optimization target only after measured component attribution.

## A/B discipline

Baseline and candidate must keep workload, dataset, model, quantization, context, provider and evidence rules constant unless the experiment explicitly studies that variable. One experiment — one variable where practical.

## Statistical interpretation

- report distribution, not one lucky run;
- do not hide failed/blocked tasks;
- no synthetic record may be labelled owner/live;
- CI Linux, CI Windows and owner Windows are separate evidence rows;
- significant performance gains are rejected if verified success, safety or recovery regress.

## Expected artifacts

- `PERFORMANCE_BASELINE_<SHA>.json`
- `PERFORMANCE_CANDIDATE_<SHA>.json`
- `PERFORMANCE_DELTA_<BASE>_<CANDIDATE>.md`
- profiler traces/artifacts bound to source SHA/tree
- updated audit synthesis and README status only after evidence.