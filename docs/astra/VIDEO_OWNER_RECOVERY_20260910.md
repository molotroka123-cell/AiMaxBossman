# Video Studio recovery evidence

This workstream started at `c7e75cc3c0a636d61f6fc8fb3e476a210d1320d3`.
Its local runs are diagnostic evidence; release acceptance must rerun against
the final convergence SHA and the wheels built from that SHA.

| Finding | Reproduction / negative control | Fix and positive control |
|---|---|---|
| MF-033, P2: unexplained native `ValueError` | Send `Склей эти два видео` without media: `/chat/{id}/run` accepted it, then native execution failed with only `executor failed: ValueError`. Unsupported subtitles behaved similarly. | Validate the existing edit plan before enqueueing and again at worker admission. Return actionable refusal while preserving the draft/project; the chat offers **Открыть сохранённый проект**. Import real media and retry the original draft: one run, verified timeline. |
| VIDEO-TRUTH-01, P1: unsupported subaction silently omitted | `Открой этот проект и замени музыку` entered the open-only executor; stitching with a subtitle request did not request subtitle work. | The native preflight refuses these unsupported effects with `VIDEO_COMMAND_REQUIRED`; no timeline mutation or successful task is created by the start request. Explicit editor commands and trained agents retain their existing paths. |
| VIDEO-RACE-01, P1: concurrent starts | Two requests both observed a draft. An instrumented barrier released both before the real DB enqueue: two task runs were created. | Use the existing atomic `enqueue(only_if_draft=True)`. The same concurrent negative control now observes exactly one run. |
| VIDEO-RACE-02, P1: owner stop lost | Stop the draft after the route reads it but before enqueue: the old start overwrote `stopped` with `queued`. | The same DB gate preserves `stopped`, creates no run and returns an explicit 409. |

## Historical 404 evidence corrected

`POST /api/video-studio/commands` is **not obsolete**. The current frontend
constructs it as `${BASE}/commands`, where `BASE` is `/api/video-studio`.
`bcc.features.video_studio` registers `POST /commands` under that prefix.
The old statement that no UI referenced this route searched for a literal URL
and missed the template expression. Current actual HTTP command calls pass;
invalid command bodies return typed 422 responses, rather than route 404.
There is no service-worker registration in the current UI tree. This does not
prove which old process/bundle produced the historical owner's 404. A clean
installed wheel and current browser acceptance are required for that boundary.

## Acceptance that actually ran locally

`test_video_http_native_edit_preview_export_restart` starts a real BCC process
with workers and a fresh database. No provider responses or API calls are
mocked. It exercises UI asset delivery and authenticated HTTP calls, missing
media refusal, real MP4/audio import, native edit execution, command replay,
trim/undo/redo, denied import forgery, preview and export, downloaded-byte SHA256,
independent ffprobe and full ffmpeg decode, process restart, persisted session,
task/project equality and identical downloadable bytes.

The focused source run passed **50 tests**, with one existing optional local
model adapter probe skipped because it was not configured. No test or gate was
weakened. The initial new route-count assertion was corrected before product
acceptance: newer FastAPI retains included routers lazily, so counting only
top-level `app.routes` was not a valid registration check. The test now counts
the feature router and separately verifies actual HTTP dispatch.

Actual local Chromium playback did **not** run: Chromium was absent and the
standard browser download failed. `REAL_HTTP_WORKER_FFMPEG` is not browser
playback evidence. The existing full browser owner test remains mandatory and
a new browser test checks visible refusal plus opening the preserved project.

## Installed artifact gate

The shared `EditorServer` fixture accepts:

- `BCC_ACCEPTANCE_PYTHON`: absolute interpreter path in the clean installed venv.
- `BCC_ACCEPTANCE_SOURCE_SHA`: exact 40-character release SHA.

In that mode, BCC starts with `python -I`, an external working directory, no
`PYTHONPATH`, its own packaged UI, and package imports underneath the venv.
The embedded `bcc/_build.json` source SHA must match the expected SHA. The
fixture saves `acceptance-runtime.json`; video result JSON includes that proof.

With those variables set, the same command is the HTTP, real browser and
fresh-process acceptance target:

```sh
python -m pytest -c command-center/pyproject.toml command-center/tests/test_editors_user_acceptance.py -q
```

The test runner needs the declared pytest/browser dependencies and FFmpeg.
The target venv needs only the installed release artifacts and their production
dependencies. Browser playback, Windows and owner model/hardware remain
separate gates; this document does not label them PASS.
