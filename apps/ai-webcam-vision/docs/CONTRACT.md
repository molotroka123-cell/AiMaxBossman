# Control contract

BOSSMAN is the control plane. This application is the workload. The only
coupling between them is this HTTP surface:

* the app imports nothing from `bcc.*` or any BOSSMAN package
  (`tests/test_independence.py` enforces it mechanically);
* BOSSMAN imports nothing from `ai_webcam_vision`;
* the app pushes no frames anywhere — it exposes structured events and metrics.

## Versioning

`GET /api/v1/capabilities → app.contract` is the contract version (`1.0`).
Breaking changes bump it; additive fields do not.

## Authentication

If `AWV_API_TOKEN` is set, every endpoint except `GET /healthz` requires
`Authorization: Bearer <token>` and is compared in constant time. If it is not
set, the service is unauthenticated and must stay bound to `127.0.0.1` or a
private VPN interface.

## Calls

### health

```
GET /healthz          -> {"status": "...", "app": {...}}          (liveness)
GET /api/v1/health    -> full readiness report
```

`status` is one of `ok`, `degraded`, `unavailable`. `blockers` lists the exact
reasons (missing ffmpeg, missing baseline, source unavailable, CRM
unavailable). The camera, CRM and analyzer blocks each declare whether they
are real or mock.

`health_state` is the actionable form and comes from a closed vocabulary:

| `health_state` | means | what an owner does |
|---|---|---|
| `healthy` | every component is answering | nothing |
| `degraded` | something is stale or not yet known | watch |
| `camera_offline` | captures are failing | check the camera and the link |
| `crm_unavailable` | the CRM is configured but not answering | call the CRM vendor |
| `detector_unavailable` | no ffmpeg, or no empty-room baseline | install ffmpeg / capture a baseline |

Precedence is worst-first: without a detector nothing else matters, and a
dead camera outranks a dead CRM because there is no evidence at all.
`components.camera`, `components.crm` and `components.detector` each carry
`state`, `detail`, `checked_at` and `consecutive_failures`. A CRM that is
switched off reports `disabled` — a configured decision, not an outage.

`runtime` reports the persistent loop (`AWV_RUNTIME_ENABLED`): state, cycles,
consecutive failures, recoveries and the capped backoff ceiling.

### capabilities

```
GET /api/v1/capabilities
```

Job types, endpoint map, camera/CRM/model descriptors, compute mode, ffmpeg
availability, limits (queue size, sample-rate ceiling, timeouts, retry backoff
sequence), privacy posture and the full non-secret configuration.

### jobs.create

```
POST /api/v1/jobs
{"type": "probe" | "baseline" | "sample" | "observe" | "snapshot",
 "params": {"duration_seconds": 10, "max_samples": 5}}
-> 202 {"id": "...", "status": "queued", ...}
```

| type | does | artifacts |
|---|---|---|
| `probe` | one connectivity check against the source | — |
| `baseline` | capture the empty-room reference | `baseline` |
| `sample` | one frame → evidence → CRM → state → stored observation | — |
| `observe` | run the sampling loop for `duration_seconds` | — |
| `snapshot` | privacy-safe still (requires `AWV_SNAPSHOTS_ENABLED=true`) | `snapshot` |

### jobs.status / jobs.cancel

```
GET  /api/v1/jobs/{job_id}         -> queued|running|succeeded|failed|cancelled
POST /api/v1/jobs/{job_id}/cancel  -> {"cancelled": bool, "job": {...}}
GET  /api/v1/jobs?limit=50
```

`error` is always scrubbed; `error_code` is machine-readable
(`capture_failed`, `capture_timeout`, `dependency_missing`, `baseline_missing`,
`privacy_denied`, `egress_blocked`, `internal_error`, `cancelled`).

### artifacts.list

```
GET /api/v1/artifacts?job_id=&limit=100
```

Artifacts are metadata records (id, job, kind, path, bytes, meta). The service
does not serve artifact bytes over HTTP — a blurred still is still a picture of
a treatment room, and shipping it through the control plane would defeat the
privacy posture.

### metrics / resources

```
GET /api/v1/metrics
```

Counters (frames captured/analyzed/dropped, capture failures, retry sleeps,
reconnects, observations stored), queue statistics, motion state, source
health, compute mode and process resources (RSS, threads, fds, children).

```
GET /api/v1/rooms/{room_id}/metrics/today
```

Seconds by state, clinical seconds, occupied seconds, `monitored_seconds`
(time actually covered by observations), `unavailable_seconds` and
`skipped_gaps` so a sparse day cannot be mistaken for a busy one.

Utilisation is reported against two explicitly named denominators, never one
unlabelled number: `utilisation_of_monitored` (clinical time over observed
time) and `utilisation_of_window` (clinical time over the calendar day).
Dividing by a whole day and calling that "utilisation" understates the room
every morning and is not comparable between days.

The day is cut on `AWV_TIMEZONE`'s midnight, and the timezone is echoed in the
payload. One instant produces at most one observation, so a replay after a
worker restart cannot double-count.

### motion capabilities

`capabilities.motion` declares what actually drives the sampling rate:
`webhook` (implemented, vendor-neutral), `onvif_subscription`
(`implemented: false`, `verified_on_tapo_c200: false`, `evidence: NOT RUN`)
and `frame_difference_fallback` (`implemented: false`).

### motion ingress

```
POST /hooks/motion {"source": "onvif-bridge"}
```

Vendor-neutral. Opens the fast-sampling window for `AWV_MOTION_HOLD_SECONDS`;
further motion extends it. Do not scrape mobile push notifications.

## Error shape

```json
{"error": "human readable, scrubbed", "code": "capture_timeout"}
```

HTTP status: 400 config/bad request, 401 unauthorised, 403 privacy/egress
denied, 404 unknown job, 409 baseline missing, 413 body too large,
502 capture failed or stale frame, 503 dependency missing, 504 capture timeout,
502 CRM schema error.

Codes: `config_error`, `privacy_denied`, `egress_blocked`, `baseline_missing`,
`capture_failed`, `capture_timeout`, `stale_frame`, `dependency_missing`,
`crm_schema_error`, `internal_error`.
