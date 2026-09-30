# Owner breaker session — evening setup (closure 2026-09-08)

What the owner does, in order, and what each step proves. Nothing here needs an
environment variable set by hand except the OpenHands sidecar command, and the
UI says so when it is missing.

| Step | Owner action | What must be visible | Where it is checked |
|---|---|---|---|
| 1 | `start-bossman.ps1` (or `start-bossman.sh`) | venv created/updated, `bossman doctor` table with PASS/WARN/BLOCKED, then the desktop window | `scripts/bossman_doctor.py` — now includes an **openhands** row (runtime importable, `BOSSMAN_OPENHANDS_COMMAND` set); BLOCKED aborts the launch |
| 2 | Windows UI opens (`bcc.desktop`) | login with the token the launcher prints; shell visible | `tests/test_ux2_*` |
| 3 | OpenRouter → paste key → **Connect** | provider created, key checked, catalog loaded (count shown); a 502/503 is shown with the upstream reason, not as a dead button | `ui/pages/openrouter.js`, `test_feat_openrouter*.py` |
| 4 | Models → probe | health per model (healthy / silent / throttled / unauthorized / unmeasured), capabilities probed (chat, tools, structured output, streaming) | `bcc/model_health.py`, `bcc/v2/capability_probe.py`; streaming probe reads `status`, the answer path reads `completion` |
| 5 | Practical roster | agents pick healthy models; the Web Designer inspector shows the same registry with health and a server default | `/api/web-designer/models`, `test_web_designer_model_choice.py` |
| 6 | Coding → readiness banner | either «Новая задача агенту» enabled, or the exact reason (runtime not installed / sidecar command not set) | `/api/coding-tasks/readiness`, `test_coding_tasks.py` |
| 7 | Governor → testing period → START OWNER BREAKER SESSION | the session id appears in the journal; every click, refusal and dead click is recorded and can be published to GitHub | testing-period feature (journals `docs/testing/sessions/…`) |

## The breaker corpus the owner should run (from the audits)

Each row names the historical failure and the expected state on the closure code.

| # | Task text (verbatim where it failed) | Historical outcome | Expected now |
|---|---|---|---|
| B1 | «Перечисли что ты умеешь делать на компе моём в новом текстовом документе» | `completed`, `result=""` (task 44) | contract `TERMINAL_FILE_ACTION`: either a real file written through the terminal tool, or `failed` with `action_contract/no_verified_action`; never `completed` with an empty result |
| B2 | «Открой higgsfield сгенерируй в режиме unlimited видео» | `completed`, `result=""` (task 22) | browser contract for `higgsfield.ai`; a challenge/login on the site → `paused` «Нужно действие владельца»; never `completed` on prose |
| B3 | «Открой в браузере youtube какую то мелодраму на русском» | `completed` with a CAPTCHA excuse (task 45) | `paused` with `WAITING_FOR_OWNER_CHALLENGE`; clear the CAPTCHA in the visible browser, press **Resume** on the browser session → the same run continues; `completed` only when the page is a `youtube.com/watch` URL |
| B4 | «Посчитай 17*23. Не используй инструменты и не пиши файлы.» | `failed` `no_verified_action` after two GLM answers (run #33) | `completed` with the text answer (391) |
| B5 | Apps → enable → start File Commander → stop → disable → start | bare 409 | 403 with «управление приложениями выключено политикой владельца» and a hint; start works while enabled |
| B6 | Web Designer: save `SAVED`, select heading, set `EDITED`, Apply, Apply | second Apply reverted to `SAVED` | both Applies leave `EDITED`; inspector field shows `EDITED` |
| B7 | Web Designer: choose GLM in the inspector, AI edit | no choice existed | toast names the chosen model; `chosen_by=owner` |
| B8 | Coding → «Новая задача агенту» on a repo inside the roots | no path existed | task card `running` → `completed`/`blocked`/`failed` with diff, evidence and «песочница удалена»; the source repository is unchanged |
| B9 | Video Studio: create → import → edit → preview → export → open the file → reopen the project | 409 on thumbnails, `executor failed: ValueError` | export file exists and decodes; any refusal names its reason |
| B10 | Stop a task while the model is mid-call | (regression) | `stopped`; no re-queue |

## Still owner/live only after this closure

* Real OpenRouter/GLM behaviour (catalog, streaming, structured output) — the
  contract tests are green on fixtures; the wire is the owner's.
* Real OpenHands SDK run (the closure tests drive a scripted sidecar over the same
  contract).
* Windows file-lock behaviour of the sandbox cleanup (mechanism proven with a
  simulated lock; the retry/reporting path is what the owner would see).
* Higgsfield authenticated generation — OWNER_LIVE_REQUIRED; no platform control
  is bypassed, a challenge is a state.
* Intelligence Preservation: the gate still requires a genuine current same-model
  measurement (`docs/benchmark/intelligence-preservation-current.json` absent) →
  EXTERNAL_EVIDENCE_REQUIRED. It was not weakened.
