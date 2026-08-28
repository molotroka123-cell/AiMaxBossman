# Privacy posture

## Denied by design (no implementation exists)

* face identification;
* patient identification from pixels;
* audio capture;
* continuous raw video retention.

Setting `AWV_FACE_IDENTIFICATION`, `AWV_PATIENT_IDENTIFICATION`,
`AWV_CAPTURE_AUDIO` or `AWV_RECORDING_ENABLED` to `true` makes startup fail
with `privacy_denied`. These are not decorative flags: `tests/test_config.py`
and `tests/test_privacy_stage2.py` assert the failure, including that the
process itself exits non-zero rather than continuing.

`AWV_RECORDING_ENABLED` is in that list deliberately. There is no recorder in
this build, so accepting the flag and reporting `recording_enabled: true` over
the API would tell an owner the clinic is recording when nothing is. A flag
that does nothing is worse than no flag.

Audio is denied in the argument vector, not only in configuration: every
ffmpeg invocation carries `-an`, so a source that has an audio track is
decoded without one. `tests/test_privacy_stage2.py` proves it against a real
fixture that does have audio, and greps the source tree for face- and
audio-capture machinery — denied by design means there is nothing to switch
on.

## Off by default (switchable, and the switch works)

| Flag | Default | Effect when off |
|---|---|---|
| `AWV_SNAPSHOTS_ENABLED` | `false` | `snapshot` jobs fail with `privacy_denied`; no image file is ever written |
| `AWV_TELEMETRY_ENABLED` | `false` | nothing is sent anywhere |
| `AWV_CRM_EGRESS_ENABLED` | `false` | the HTTP CRM refuses to transmit; `generic_http` will not even start |

`tests/test_privacy_defaults.py` includes a socket guard proving a default run
opens no outbound connection.

## What is stored

| Artifact | Content | Permissions |
|---|---|---|
| `baseline.npy` | one 160x90 grayscale array of the empty room | `0600` |
| `vision.sqlite3` | structured observations, jobs, artifact records | `0600`, directory `0700` |
| `snapshots/*.jpg` | only if explicitly enabled: downscaled, grayscale, blurred | `0600`, directory `0700` |

Analysis runs at 160x90 grayscale. At that resolution a face is a handful of
pixels — the privacy property comes from the pipeline geometry, not from a
policy statement.

Snapshots are downscaled, converted to grayscale and Gaussian-blurred **inside
ffmpeg**, so the full-resolution image never enters this process' memory or
disk. Retention is a fixed file count (`AWV_SNAPSHOT_RETENTION`).

## Identity

Employee and clinician identity comes from the CRM roster/shift/room
assignment. It is never inferred from pixels. When no CRM is configured the
context is marked `available: false` and every stored observation records that,
so "the CRM said there is no appointment" and "there is no CRM" can never be
confused.

## Credentials

Camera and CRM credentials are read from the environment, held in `Secret`
objects with no revealing `repr`, embedded into a URL in exactly one function,
and scrubbed out of logs, API responses, exception text and stored state. A
canary test drives a uniquely identifiable password through a failing real
connection and then searches every channel for it.

Residual risk, stated plainly: the stream URL is passed to ffmpeg as a command
line argument, so it is visible in `/proc/<pid>/cmdline` to users on the same
host who can read that process. Mitigate by running the service as a dedicated
unprivileged user. The application's own logs never contain the argument — the
URL is replaced with `<stream-url>` before logging.

## Legal

Before production use in a Czech/EU dental clinic, review workplace monitoring
and patient-data requirements: lawful basis, worker notice and transparency,
works-council/employee consultation where applicable, retention limits, access
control and the record of processing. This document describes engineering
defaults, not legal compliance.
