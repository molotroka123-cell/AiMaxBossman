# AI WebCam Vision

Operational analytics for one fixed clinic camera (TP-Link Tapo C200 class).
An **independent workload service**: it imports nothing from BOSSMAN, and
BOSSMAN drives it through the HTTP contract described below.

Camera evidence says what physically happened in the room. The CRM says what
was supposed to happen and who was assigned. The product is the fusion of the
two — movement alone is never reported as work.

## What it does

```
motion event (ONVIF bridge / NVR / edge script)
   -> POST /hooks/motion               vendor-neutral wake signal
   -> sampling window opens            active interval instead of idle interval
   -> ffmpeg pulls one 160x90 gray frame per sample
   -> bounded frame queue              drop-oldest, never grows
   -> baseline + zone evidence         room / chair / work-zone change
   -> CRM room context                 shift, appointment, planned/confirmed service
   -> operational state + hysteresis
   -> SQLite timeline -> daily metrics
```

States: `EMPTY`, `TRANSIT`, `STAFF_NONCLINICAL`, `PREP`, `CLINICAL_WORK`,
`TURNOVER`, `IDLE_OCCUPIED`, `UNKNOWN`.

State changes go through a temporal machine, not a sample counter: minimum
dwell is measured on the clock (longer for `CLINICAL_WORK`, longer still in
evidence terms for `TURNOVER`, which additionally requires recent real
occupancy), a short detector dropout holds the current state instead of
cutting one procedure in two, and the transition table refuses impossible
jumps — `EMPTY` never becomes `CLINICAL_WORK` in one step.

## Two ways to run it

* **jobs only** (default): the control plane asks for `probe`, `baseline`,
  `sample`, `observe`, `snapshot` and gets answers;
* **persistent runtime** (`AWV_RUNTIME_ENABLED=true`): one long-lived loop
  samples the room for as long as the process lives. It survives a camera
  that disappears and a network that drops, backs off with a **capped** delay
  rather than a finite attempt budget, wakes immediately on shutdown, and
  never spins — every cycle waits. `health.runtime` reports its state.

## Install and run

```bash
cd apps/ai-webcam-vision
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # then edit; secrets come from the environment
ai-webcam-vision check        # prints capabilities, exits
ai-webcam-vision serve        # starts the HTTP service
```

`ffmpeg` is required for `file` and `rtsp` camera modes. It is looked for in
four places, in order: an explicit `AWV_FFMPEG_PATH`, `PATH`, `AWV_FFMPEG`,
and the static binary shipped by the `imageio-ffmpeg` package. `health.ffmpeg`
reports which one was used (`source`) and, when none was, every place that was
searched. An explicit `AWV_FFMPEG_PATH` that does not resolve is a failure, not
a reason to fall back — a typo must surface. If nothing is found the service
says so (`/api/v1/health` → `status: unavailable`) instead of pretending to
work.

## Camera modes — never ambiguous

| `AWV_CAMERA_MODE` | pixels from | ffmpeg used | `is_mock_camera` |
|---|---|---|---|
| `mock` (default) | generated in-process | no | `true` |
| `file` | a local video fixture | yes | `true` |
| `rtsp` | the physical camera | yes | `false` |

Every API response that mentions the camera carries `kind`, `is_mock_camera`
and `uses_real_transport`. The same applies to the CRM (`kind`, `is_mock`) and
to the analyzer (`heuristic_pixel_baseline`, explicitly *not* a model).

## Control contract

| Contract call | Endpoint |
|---|---|
| health | `GET /api/v1/health`, `GET /healthz` |
| capabilities | `GET /api/v1/capabilities` |
| jobs.create | `POST /api/v1/jobs` — `{"type": "probe\|baseline\|sample\|observe\|snapshot", "params": {}}` |
| jobs.status | `GET /api/v1/jobs/{job_id}` |
| jobs.cancel | `POST /api/v1/jobs/{job_id}/cancel` |
| jobs.list | `GET /api/v1/jobs` |
| artifacts.list | `GET /api/v1/artifacts?job_id=` |
| metrics / resources | `GET /api/v1/metrics` |
| room metrics | `GET /api/v1/rooms/{room_id}/metrics/today` |
| motion ingress | `POST /hooks/motion` |

Set `AWV_API_TOKEN` to require `Authorization: Bearer …` on everything except
`/healthz`. See `docs/CONTRACT.md`.

## Engineering properties

* **Credentials never leave the process.** `Secret` has no revealing `repr`,
  URLs are assembled in exactly one function, and a global scrubber removes
  registered secret values from logs, API payloads, exception text and stored
  state. Proven by a canary test (`tests/test_secret_hygiene.py`).
* **Secrets come from the environment only** — there is no CLI flag and no
  literal that can supply a camera password.
* **Bounded timeouts** on connect and on every ffmpeg invocation; a hung child
  is killed and reaped.
* **Bounded reconnect** with exponential backoff, capped delay and a finite
  attempt budget; a missing binary is not retried.
* **Controlled sample rate** with a hard ceiling (`AWV_MAX_SAMPLE_RATE_HZ`).
* **Bounded frame queue** — drop-oldest with byte and count budgets; memory
  does not grow with a fast producer.
* **Clean shutdown** — runtime loop stopped, jobs cancelled, tasks awaited,
  sources closed.
* **Health names the fault.** `health_state` is one of `healthy`, `degraded`,
  `camera_offline`, `crm_unavailable`, `detector_unavailable`, with a
  per-component breakdown. A CRM that is switched off is `disabled`, not an
  outage.
* **Stale frames are dropped.** A frame older than `AWV_MAX_FRAME_AGE_SECONDS`
  describes the past and is never stored as the room's current state.
* **CRM answers are validated.** JSON booleans must be booleans (`bool("false")`
  is `True` in Python, and that is how an empty room becomes an active
  appointment), overlapping appointments are resolved by priority rather than
  by list order, dated answers are marked stale, and requests are retried with
  a bounded, capped backoff.
* **Days are cut on the clinic's midnight** (`AWV_TIMEZONE`), and utilisation is
  reported against two explicitly named denominators — observed time and the
  calendar window.
* **CPU/GPU is reported honestly**: the pipeline is CPU integer arithmetic and
  says so even on a GPU host.
* **Privacy closed by default**: no recording, no snapshots, no telemetry, no
  CRM egress. Audio capture, face identification and patient identification
  are denied by design — enabling them fails startup.

## Layout

```
src/ai_webcam_vision/
  secretstore.py     Secret, SecretUrl, the only URL assembly point, scrubber
  errors.py          errors whose messages are scrubbed at construction
  logging_setup.py   redacting log handlers
  config.py          environment configuration and validation
  transport/         ffmpeg runner, RTSP/file source, synthetic mock, retry
  pipeline/          bounded queue, baseline analysis, motion gate, classifier, snapshots
  crm/               disabled / mock / real HTTP clients
  storage/           SQLite timeline, jobs, artifacts
  runtime/           job manager, resource reporting, VisionService composition root
  api/               FastAPI control contract
tests/               207 tests; see docs/APP1_AUDIT_STAGE2.md for the audit
```

## Honest status

There is no physical Tapo C200 in this environment and no clinic CRM. The
whole transport boundary is tested against real ffmpeg using generated video
fixtures and a refused RTSP endpoint; the physical camera smoke test is **NOT
RUN — blocked by hardware**, and so is ONVIF event subscription, which is
**not implemented** in this build. `capabilities.motion` says so in the
payload. The real CRM is **NOT RUN** — only the mock, the schema, the retry
and the egress guard are exercised. Details and evidence levels:
`docs/APP1_AUDIT_STAGE2.md` and `docs/APP1_REPORT.md`.
