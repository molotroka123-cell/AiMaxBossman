# Owner test-session clustering — 2026-09-08 closure

Source: the owner's testing-period journals published on `audit/cloud-qa-20260908`
— `2026-09-08_184119__5893ffba6724.jsonl` (13 728 records, commit `966c16c`) and the
later superset `2026-09-08_185214__2fdf5629d0d9.jsonl` (14 614 records, commit `2903b5b`).
The superset is clustered below; every cluster was also present in the 5893 file
unless marked `2fdf only`. Raw counts are events, not bugs: one owner action often
produces several rows (request + refusal + dead click), so clusters are by endpoint,
control, error signature, task and subsystem, and each cluster has one root cause.

Journal facts that bound every conclusion:

| Fact | Value |
|---|---|
| Records | 14 614 (5893: 13 728) |
| Period | 2026-09-04 12:21 → 2026-09-08 18:52 UTC |
| Server sessions | 25 (Python 3.14.3 ×10, 3.13.1 ×10, 3.12.14 ×5; Windows 11) |
| Loaded source SHA | **not recorded in the journal** (`session.env` carries no commit) — every "FIXED?" below is a statement about the closure code, not about the owner's runtime |
| `http.error` | 71 |
| `ui.refused` | 133 (114 of them `status=0`: the UI could not reach the server at all) |
| `ui.dead_click` | 41 |
| Tasks | created 23 · completed 26 · blocked 13 · stopped 3 · failed 2 |

Fixed-on-closure rows cite the fix commit `69df482c6ab94f04b17715d9e2ceb4a695d20590`
(FIX_SHA) unless an earlier night-line commit is named.

## Clusters, owner-visible first

| # | Cluster | COUNT | FIRST | LAST | SUBSYSTEM | ROOT_CAUSE | DUPLICATE_OF | FIXED? | FIX_SHA | OWNER_RETEST_REQUIRED? |
|---|---|---|---|---|---|---|---|---|---|---|
| C1 | `task.completed` with **empty result** (tasks 16, 22, 24, 44) | 4 | 09-07 14:10 | 09-08 18:47 | engine / finalize | Generic tasks without a declared obligation finalized on the model's empty answer; two of them (22, 44) also lacked the action contract their text implied (higgsfield → browser; «в новом текстовом документе» → file) | MF-001 | YES — `EMPTY_RESULT` refusal in `finalize_task`; classifier gaps closed | 69df482 | YES — re-run tasks 22 and 44 verbatim; expected `failed`/contract gate, never `completed` with `result=""` |
| C2 | `task.completed` whose result is a **CAPTCHA excuse** (tasks 15, 28, 43, 45) | 4 | 09-07 14:07 | 09-08 18:50 | review_gate / verification / engine | Browser expectation `url_contains=youtube.com` was VERIFIED on a reCAPTCHA page; no challenge state existed between "verified" and "failed"; the model's "press Resume" prose was the task result | MF-002 | YES — `BLOCKED` verification status, task `paused` with `WAITING_FOR_OWNER_CHALLENGE`, Resume bound to the run, deeper `youtube.com/watch` goal, excuse regex refusal | 69df482 | YES — task 45 verbatim on the visible browser: expected `paused` with a named reason, Resume continues the same run |
| C3 | `evaluation.completed PASS "browser:session ✓"` | 5 | 09-05 15:37 | 09-08 18:50 | review_gate | Same mechanism as C2 (domain-only evidence); 1 of the 5 was a legitimate `example.com` open | C2 | YES (same fix) | 69df482 | covered by C2 retest |
| C4 | `task.blocked BLOCKED_CAPABILITY_UNAVAILABLE` «Исполнитель не выбран» (tasks 19–21, 25–27, 33–35, 37–39, 46) | 13 | 09-07 14:14 | 09-08 18:52 | engine admission / task creation UI | Tasks created with `agent_id=null` (e.g. task 46 «Сайт: Кошачья Моча» from a page that does not pick an agent); admission refuses honestly instead of running nothing | — | Behaviour is the intended fail-closed admission (`test_executor_admission.py`); the creation path that omits an agent is a UX gap, not fixed here | — | YES — confirm the creating page offers an agent; if a page creates agentless tasks by design, it must say so |
| C5 | `ui.refused status=0` on `/api/system`, `/api/models`, `/api/tasks?…`, `/api/approvals?…`, `/api/agents`, `/api/agentmap`, `/api/activity`, `/api/resources`, `/api/missions`, `/api/apps`, `/api/providers`, `/api/models/{1,2}/check` | 114 | 09-05 17:01 | 09-08 18:30 | desktop launcher / server lifecycle | The UI polled while the server was not listening (restarts between the 25 sessions); not an API error — no server-side row exists for these | — | Not a defect of the API; reconnect banner exists (`test_ux2_reconnect.py`) | — | NO (observe that the banner appears during a restart) |
| C6 | `POST /api/openrouter/1/connect` → 502 (×6), `/sync?force=true` → 503, `/openrouter/5/connect` → 400 | 8 | 09-06 20:50 | 09-07 11:34 | openrouter provider | Upstream/key failures on the owner's network at that time; all on Python 3.14.3 sessions | night map A | Cannot be fixed from the repository; classification of provider vs model failure is tested (`test_streaming_contract.py`, `test_model_health.py`) | — | YES — live Connect with the evening key (OWNER_LIVE) |
| C7 | `POST /api/apps/<9 apps>/start|stop` → 409, incl. `file-commander-mini` ×7 | 27 | 09-05 20:19 | 09-08 18:21 | apps_control | Control disabled by policy and every refusal was a bare 409 indistinguishable from a port conflict | night map 12 | YES on the night line before this closure (`46827a4`: 403 `APPS_CONTROL_DISABLED` + hint; policy via API, no env/restart ritual); regression pinned by `test_apps_owner_path_regression.py`; the owner's 09-08 runtime still returned 409 → it predates the fix | 46827a4 | YES — Apps → enable → start File Commander → stop → disable → start refused with the named reason |
| C8 | `GET /health`, `/healthz`, `/api/health`, `/ui` → 404 | 7 | 09-07 11:03 | 09-07 11:08 | http | No liveness URL; health lives at `/api/system` | night map i | Not fixed (deliberately untouched P2) | — | NO |
| C9 | `GET /api/browser/sessions/1/{state,screenshot}` → 404, `POST /api/browser/sessions/2/act` → 403 | 6 | 09-04 12:26 | 09-05 15:31 | browser | Session ended / policy refusal shown as raw codes | night map 7 | YES earlier (`test_browser_navigation_ui.py`) | (night) | NO |
| C10 | `POST /api/web-designer/projects/1/ai-edit` → 502 | 2 | 09-07 14:13 | 09-07 14:13 | web_designer | Local model endpoint down at 14:13 (doctor logged 11435 DOWN); honest 502 «модель недоступна» | night map 14 | Honest error existed; **model choice** did not — added (`/api/web-designer/models`, `model_id`) | 69df482 | YES — pick GLM in the inspector, run an AI edit |
| C11 | `GET /api/trading-lab/status` → 500 `ModuleNotFoundError: bossman_v3` | 1 | 09-06 20:53 | 09-06 20:53 | trading_lab | Hard import of the core package | night map 16 | YES earlier (`ec04e92`); `test_trading_lab_unwired.py` green on the closure SHA | ec04e92 | NO |
| C12 | Video Studio `thumbnail`/`waveform` → 409 | 5 | 09-07 14:11 | 09-08 18:46 | video_studio | Derivative not prepared yet, returned as a bare 409 | night map 13 | YES earlier (reason `derivative_not_prepared`) | (night) | NO |
| C13 | `POST /api/video-studio/commands` → 404 | 1 | 09-08 18:33 | 09-08 18:33 | video_studio UI | UI called a route that does not exist on that runtime (`2fdf only`) | — | Not reproduced on the closure code: no UI code references `/api/video-studio/commands` | — | YES — Video Studio create/import/timeline/edit/preview/export on the visible machine |
| C14 | `task.failed 40 "executor failed: ValueError"` | 1 | 09-08 18:33 | 09-08 18:33 | video executor | A native executor raised; the run failed honestly with the exception class only | — | Not fixed (message names the class, not the cause) — P2 | — | YES (same Video Studio path as C13) |
| C15 | `POST /api/coding-sessions` → 400 | 1 | 09-08 18:48 | 09-08 18:48 | coding_sessions | Owner tried the Coding page; the only action there was a worktree session with a path outside the roots (`2fdf only`) | MF-011 | YES — the page now has the agent-task path with readiness and roots shown | 69df482 | YES — Coding → «Новая задача агенту» |
| C16 | dead click `button#bcc-testing-publish «Отправить в GitHub»` (10 pages) | 17 | 09-04 12:25 | 09-07 14:05 | testing_period UI | Publish button gave no visible feedback within 900 ms while the upload ran | — | Not fixed here (cosmetic; the publishes themselves succeeded — the journals exist) — P3 | — | NO |
| C17 | dead clicks `button «Далее»` ×4, `«Новый провайдер»` ×3, `«Существующий»` on `#/models` | 8 | 09-06 20:51 | 09-07 14:04 | models wizard | Wizard steps on the sessions where OpenRouter connect was returning 502 (C6): the next step could not render | C6 | Follows C6 | — | with C6 |
| C18 | dead click `button «▶»` on `#/video-studio` | 6 | 09-07 14:12 | 09-07 14:22 | video_studio | Play pressed while the derivative was not prepared (C12) or the stall guard held | C12 | see C12; playback stall has `test_video_studio_playback_stall.py` | (night) | YES (Video Studio owner path) |
| C19 | dead clicks `#think-close`, `#palette-open`, `#stale-now`, `«Все приложения»`, `«Проверить снова»`, `«Подставить в строку команды»` | 9 | 09-04 12:25 | 09-08 18:47 | UI shell | Feedback-less controls (state toggles that change nothing visible within 900 ms) | — | Not fixed — P3 cosmetic | — | NO |
| C20 | `mission.failed «остановлено оператором»` | 4 | 09-07 22:52 | 09-08 18:22 | missions | Owner stopped missions by hand | — | Not a defect | — | NO |
| C21 | `action_contract.blocked` task 6 (`GITHUB_ACTION`, has_tools) and `no_verified_action` FAILs | 3 | 09-05 17:03 | 09-05 17:04 | action_contract | The gate refused text-only "pushed" claims — the contract working as designed | — | Not a defect. The **negation** misfire (Astra F7, owner run #33 on 09-08) is a different case, fixed in `69df482` | 69df482 | YES — «Посчитай 17*23. Не используй инструменты» completes with text |

## What the journal proves and what it cannot

* It proves the two P0 patterns on the owner's machine repeatedly (C1 ×4, C2 ×4), not
  once, across three different Python versions — the runtime SHA is unknown, but the
  same pattern was then reproduced on the closure HEAD `45027d3` by
  `tests/test_p0_completion_truth.py` before the fix, so the fix targets a defect
  that was present in the code, not only in a stale build.
* It cannot prove any fix: none of the sessions ran the closure code. Every row
  marked OWNER_RETEST_REQUIRED=YES is part of the evening breaker.
* 114 of the 133 UI refusals are the UI talking to a server that was not up. They
  are not API defects and are not counted as such anywhere in this closure.
