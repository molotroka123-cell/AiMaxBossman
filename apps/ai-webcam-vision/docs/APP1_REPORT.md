# APP1 — honest build report

> **Superseded in part by `APP1_AUDIT_STAGE2.md` (2026-08-28).** This document
> describes the build as it stood before the stage-2 audit. Three claims below
> did not survive it and are corrected here rather than quietly edited away:
>
> * **Test totals.** 102 → **207**. The per-file table below is the old one.
> * **ffmpeg discovery.** "The ffmpeg-dependent tests found a real binary here
>   through the `imageio-ffmpeg` dev dependency" was true of the *tests* and
>   false of the *application*: the app looked only in `PATH` and reported the
>   camera as unsupported on this very host. Fixed; the tests no longer
>   resolve the binary on the app's behalf.
> * **"A new state must repeat `AWV_DEBOUNCE_SAMPLES` times"** was hysteresis
>   in name only — it counted samples, not time. Replaced by a temporal state
>   machine with wall-clock dwell, dropout tolerance and a legal-transition
>   table.
>
> Everything the audit re-verified rather than fixed is marked as such in
> `APP1_AUDIT_STAGE2.md`. The evidence labels in that document
> (`LOCAL PASS` / `MOCK CRM PASS` / `REAL CRM PASS` / `REAL TAPO PASS` /
> `NOT RUN` / `FAIL`) are the current ones.

Date: 2026-08-28. Environment: Linux x86_64, Python 3.11.15, no GPU, no Tapo C200.

Evidence levels used throughout: **REAL IMPLEMENTED**, **REAL TESTED**,
**MOCK TESTED**, **STATICALLY VERIFIED**, **NOT TESTED**, **BLOCKED BY HARDWARE**.
A claim without a level does not count.

## Test totals

```
cd apps/ai-webcam-vision && python -m pytest -q
102 passed in 4.4s      (0 failed, 0 skipped, 0 xfail)
```

| File | Tests | Covers |
|---|---:|---|
| `test_secret_hygiene.py` | 13 | Secret opacity, single URL assembly point, scrubber, log filter, canary |
| `test_pipeline.py` | 16 | motion gate, baseline analysis, classifier, hysteresis |
| `test_api_contract.py` | 14 | health, capabilities, jobs, artifacts, metrics, auth, error shapes |
| `test_privacy_defaults.py` | 11 | closed-by-default posture, snapshot safety, egress guard |
| `test_transport_ffmpeg.py` | 9 | the real ffmpeg boundary |
| `test_config.py` | 9 | runtime env binding, validation, denied capabilities |
| `test_retry_reconnect.py` | 7 | backoff, bounded budget, fault injection, cancellation |
| `test_storage.py` | 7 | schema, connection lifetime, metrics, jobs, artifacts, permissions |
| `test_queue_bounded.py` | 6 | memory bound under a fast producer |
| `test_shutdown.py` | 6 | clean shutdown, job cancellation, child process reaping |
| `test_independence.py` | 4 | no control-plane imports, one URL assembly site |

The ffmpeg-dependent tests found a real binary here through the
`imageio-ffmpeg` dev dependency, so none of them skipped. On a machine with
neither `ffmpeg` on PATH nor that package they skip with an explicit reason —
they never fake a pass.

## Required properties, one by one

| Requirement | Status | Evidence |
|---|---|---|
| Credentials never reach logs / model context / snapshots / telemetry / exception traces | **REAL TESTED** | `test_canary_password_never_escapes`: a unique password drives a real ffmpeg connection to a refused RTSP port, then the log file, seven API responses, two failed job payloads, the SQLite file and every state file are searched. Mutation check: disabling the scrubber makes this test fail (verified in this session). |
| Secret wrapper without revealing `__repr__`/`__str__` | **REAL TESTED** | `test_secret_never_renders_its_value`, `test_secret_inside_container_repr_is_safe`, `test_secret_is_not_serialisable` |
| Single RTSP URL assembly point | **REAL TESTED** + **STATICALLY VERIFIED** | `secretstore.build_stream_url` is the only assembler; `test_only_one_url_assembly_site` scans the source tree for other sites |
| Empty-username leak (the legacy defect) | **REAL TESTED** | `test_url_assembly_masks_empty_username` |
| Secrets only from environment/config, not from arguments or code | **REAL IMPLEMENTED** + **STATICALLY VERIFIED** | `Settings.from_env` is the only ingress; `main.py` has no credential flag; `SECRET_ENV_VARS` documents the list |
| Bounded connect and ffmpeg timeouts | **REAL TESTED** | `test_capture_timeout_kills_the_child` (1 s budget honoured, elapsed < 10 s), `-timeout` passed to the RTSP demuxer |
| Hung ffmpeg is killed and reaped | **REAL TESTED** | `test_capture_timeout_kills_the_child`, `test_ffmpeg_child_does_not_survive_service_shutdown` (psutil child inspection) |
| Reconnect after RTSP/network loss, bounded attempts, exponential backoff | **REAL TESTED** (against real ffmpeg failures) + **MOCK TESTED** (fault injection) | `test_refused_rtsp_endpoint_fails_fast_and_scrubbed`, `test_retry_recovers_after_injected_failures`, `test_retry_budget_is_bounded`, `test_backoff_is_exponential_and_capped`, `test_service_counts_reconnects_and_recovers` |
| Controlled frame rate / sampling | **REAL IMPLEMENTED**, config enforced | `_sample_interval()` honours the motion gate and the `AWV_MAX_SAMPLE_RATE_HZ` ceiling; `test_sample_rate_ceiling_is_enforced` rejects a violating configuration |
| Bounded frame queue, memory does not grow | **REAL TESTED** | `test_memory_does_not_grow_with_a_fast_producer` pushes 10 000 frames through a capacity-8 queue and asserts size, retained bytes, drop count and high-water mark; `test_observe_loop_keeps_the_queue_bounded` does it through the live loop |
| Clean shutdown | **REAL TESTED** | `test_no_stray_tasks_remain_after_shutdown`, `test_running_job_is_cancelled_on_shutdown`, `test_service_is_closed_when_the_app_stops`; live server exited cleanly on SIGTERM with no orphaned children |
| CPU vs GPU is visible | **REAL TESTED** | `detect_accelerator()` reported `mode: cpu, reason: "nvidia-smi not found; no GPU devices detected", used_by_pipeline: cpu` on this host; asserted in `test_health_declares_mock_versus_real` and `test_metrics_reports_resources_and_counters` |
| Health / status endpoint | **REAL TESTED** | `/healthz` and `/api/v1/health` covered by tests and by a live server run |
| Camera real vs mock is explicit | **REAL TESTED** | every descriptor carries `kind`, `is_mock_camera`, `uses_real_transport`; asserted in API tests and stored per observation (`source_is_mock`) |
| Model/provider real vs mock is explicit | **REAL IMPLEMENTED** + **REAL TESTED** | `capabilities.model.provider == "none"`; the analyzer is declared a pixel heuristic, not ML. There is no model provider to be mocked — stating otherwise would be the lie this requirement guards against |
| CRM real vs mock is explicit | **REAL TESTED** | `CrmContext.available` separates "no CRM" from "no appointment"; `test_disabled_crm_makes_no_call_and_says_it_is_absent`, `test_mock_crm_is_always_flagged_as_mock` |
| Reconnect and fault-injection tests | **REAL TESTED** / **MOCK TESTED** | `FaultScript` injects `fail`/`timeout`/`dependency` deterministically |
| Snapshots are privacy-safe | **REAL TESTED** | `test_enabled_snapshot_is_small_grayscale_and_owner_only`: 96 px wide, grayscale, blurred in ffmpeg, `0600` file in a `0700` directory, retention enforced |
| No recording unless explicitly enabled | **REAL TESTED** | `test_recording_and_snapshots_are_off_by_default`, `test_no_snapshot_files_are_written_during_a_normal_sample` (asserts no image/video file appears anywhere in the state directory) |
| No background egress unless configured | **REAL TESTED** | `test_default_run_opens_no_outbound_socket` patches `socket.connect` and asserts no non-loopback connection during a full sample cycle; `test_http_crm_refuses_to_transmit_when_egress_is_disabled` |
| No `bcc.*` imports in business logic, no app imports inside `bcc` | **STATICALLY VERIFIED** + **REAL TESTED** | `test_no_control_plane_imports` walks the AST of every module |

## Control contract (phase 4)

**REAL TESTED** against a live uvicorn process on 127.0.0.1:8871 in `file`
mode with real ffmpeg, plus 14 automated contract tests:

* `GET /healthz` → `{"status":"degraded", ...}` before a baseline exists;
* `GET /api/v1/health` → 401 without the bearer token, 200 with it;
* `POST /api/v1/jobs {"type":"baseline"}` → `succeeded`, artifact recorded
  (`baseline`, 14 528 bytes, `npy-gray`);
* `POST /hooks/motion {"source":"onvif-bridge"}` → gate active, 90 s remaining;
* `POST /api/v1/jobs {"type":"observe", "duration_seconds":3, "max_samples":5}`
  → `succeeded`, 3 samples, 0 dropped, 0 failures;
* `GET /api/v1/metrics` → counters `frames_captured: 4, frames_analyzed: 3,
  observations_stored: 3`, queue `high_water_mark: 1`, RSS 68.9 MB, 0 children;
* `GET /api/v1/rooms/live-room/metrics/today` → seconds by state, gaps skipped;
* `GET /api/v1/artifacts` → the baseline artifact;
* SIGTERM → `Waiting for application shutdown` → `service stopped` →
  `Application shutdown complete` → process gone, port closed, no orphaned
  ffmpeg; state files `0600`/`0700`; the log file and the SQLite database
  contain neither the password nor any `rtsp://` string.

## Blocked by hardware

* Physical Tapo C200 RTSP capture, authentication, stream1/stream2 behaviour,
  night mode, and real-world frame timing — **BLOCKED BY HARDWARE**.
* ONVIF motion event subscription against the real firmware — **BLOCKED BY
  HARDWARE**.
* PTZ pose stability and baseline invalidation after camera movement —
  **BLOCKED BY HARDWARE**.
* Real network loss over the VPN (as opposed to a refused local port) —
  **NOT TESTED**; the failure classification and reconnect path it would
  exercise are covered by the refused-endpoint and fault-injection tests.
* Real clinic CRM endpoint — **NOT TESTED**; only the mock and the egress
  guard are exercised.
* Threshold calibration (`room/chair/work`) against a real treatment room —
  **NOT TESTED**. The defaults are inherited from the source pack and are
  guesses until calibrated on site.

## Known defects and limitations still present

1. **The stream URL is visible in the process table.** ffmpeg takes the URL as
   an argument, so `/proc/<pid>/cmdline` exposes it to users who can read that
   process. The application's own logs replace it with `<stream-url>`, but the
   kernel-level exposure remains. Mitigation: run as a dedicated user.
   — **STATICALLY VERIFIED**.
2. **Literal scrubbing has a minimum length of 4 characters.** A camera
   password shorter than that is masked only in URL form. Documented in
   `secretstore.MIN_REGISTERED_LENGTH`. — **STATICALLY VERIFIED**.
3. **One ffmpeg process per sample.** Simple and independently timeout-bounded,
   but it re-establishes the RTSP session every time. At sub-second sampling on
   a real camera this will be wasteful; a long-lived pipe is future work.
   — **STATICALLY VERIFIED**.
4. **No frame-difference motion fallback.** The motion gate is driven by the
   webhook only. Without a webhook the service samples at the idle interval.
   — **STATICALLY VERIFIED** (stated, not implied to work).
5. **No procedure episodes.** Observations are point samples; episode
   assembly, scheduled-vs-observed duration and per-clinician efficiency are
   not implemented. — **STATICALLY VERIFIED**.
6. **No data retention policy.** The observations table grows without pruning.
   — **STATICALLY VERIFIED**.
7. **Single-process state.** The motion gate, debouncer and queue live in
   process memory; running multiple uvicorn workers would split them. Run one
   worker. — **STATICALLY VERIFIED**.
8. **No authentication by default.** Safe only because the default bind is
   `127.0.0.1`. Setting `AWV_HOST` to a routable address without
   `AWV_API_TOKEN` is not blocked by the app. — **STATICALLY VERIFIED**.
9. **Thresholds are not calibrated.** See above.

## Resource requirements (measured here)

* RSS of the live service after a baseline plus three observations: **68.9 MB**
  — **REAL TESTED**.
* Dependencies: fastapi, uvicorn, httpx, numpy. OpenCV was dropped — ffmpeg
  emits raw grayscale frames directly, so the heaviest dependency of the source
  pack is gone. — **REAL IMPLEMENTED**.
* Analysis cost: three integer array comparisons on a 160x90 frame per sample;
  negligible on any modern CPU. — **NOT TESTED** (not benchmarked).
* Disk: baseline 14.5 KB, SQLite grows with observations, snapshots only if
  enabled. — **REAL TESTED**.
* GPU: not required and not used. — **REAL TESTED** (`used_by_pipeline: cpu`).
