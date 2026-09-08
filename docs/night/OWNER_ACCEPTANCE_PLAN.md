# BOSSMAN — owner acceptance plan

Candidate: branch `night/v7-convergence-20260908`, code SHA `72bae3e`.
Time: about 2 hours if nothing breaks. You can stop after any step — every step
records its own evidence.

**Nothing here asks you to read code.** Where a step can only be judged by you,
it says so.

---

## Before you start

1. **Start the server on its own**, not from the desktop window:

   ```
   python -m bcc.app
   ```

   Then open the window separately. (Closing the Chrome window used to take the
   server down with it; keeping them apart avoids that entirely.)

2. **Recording is already on.** A banner shows a running count of captured
   events. You do not have to arm anything. It captures: every click, dead click,
   rage click, navigation, form submit, JS error, unhandled rejection, refused
   request with its reason and timing, task transitions, approvals, review
   cycles, model and provider choices, token usage and cost, OpenHands events,
   tool calls, and resource samples.

3. **Secrets stay out of it.** The log is scrubbed on publish — known secret
   values, long high-entropy strings and authorization fields are replaced, and
   the publish result tells you how many places were cleaned. Never paste an API
   key into a task description or a chat box; enter keys only in the provider
   form.

4. Keep a note of anything you had to do **that the system should have done
   itself**. That count is the single most important number of the day.

---

## The 20 steps

### 1. Launch
Open the app. Log in.
**Expect:** the shell loads, no error banner, the sidebar shows six named
spaces. **Record:** how long until the Home screen is usable.

### 2. System and resource telemetry
Go to **Система**.
**Expect:** memory and disk are real numbers or an explicit "не измерено" — never
a plausible-looking zero. Background loops (worker, scheduler, metrics, and the
feature loops including the review sweep) each show `ok` / `starting` / `stale` /
`error`.
**Red flag:** any loop stuck on `stale` or `error`, or an invented RAM figure.

### 3. OpenRouter, live
**Модели → Добавить модель**, or the OpenRouter screen. Enter your key.
**Expect:** the key is accepted, the catalogue loads, models appear with real
state. **Red flag:** a click that does nothing, or a 403 that arrives instantly
(that used to mean a stale browser token — you should now be sent to the login
form instead of hitting a dead button).

### 4. GLM streaming
Pick a GLM model and run any short prompt.
**Expect:** text arrives progressively. If the provider cannot stream, the model
is marked **DEGRADED**, not "fine" — and the answer still arrives.
**Red flag:** silence with a green status.

### 5. A free / silent model
Attach a free model (e.g. a `:free` route) and probe it.
**Expect:** if it answers nothing, its health reads **SILENT** — not HEALTHY —
and it is not chosen for real work. Routing should fall back to a healthy model
and say that it did.
**Red flag:** an empty answer counted as a pass.

### 6. A normal mission
Create an ordinary task — e.g. *"Fix the typo in README.md and show me the
diff."*
**Expect:** it runs to a terminal state on its own.
**Record:** how many approvals it asked for, and the token count on the task's
efficiency panel.

### 7. Approvals
Watch what it asks.
**Expect:** one question per distinct real effect. Reading a file and writing a
file are two different questions; reading ten files is one. Approving with a
lease should visibly cover the later reads without asking again.
**Red flag:** the same effect asked twice, or a storm of near-identical prompts.

### 8. Autonomous completion
Let step 6 finish without helping it.
**Expect:** `completed` or an honest `failed` with a stated reason. A task must
never sit in "waiting for approval" with nothing in the approvals queue.
**Red flag:** a task you can only escape with `/stop`. **This is the single most
important check of the day** — it is the defect this whole cycle was about.

### 9. OpenHands — a real coding task
Use a **throwaway clone**, never a repository you care about.

Task: *"In this repository, create `NOTES.md` containing the line
`acceptance <today's date>`. Do not commit."*

**Expect:** the agent edits inside an isolated worktree, Bossman derives the diff
from git itself, and the result names `NOTES.md` as the changed file. The agent
must **not** create a commit, and must **not** touch anything outside the file
it was allowed to touch — if it does, the run is refused even though the edit
happened.
**Red flag:** a "done" with no diff, or changes outside the allowed path.

### 10. Video Studio
Import a short clip. Look at the thumbnail and waveform, scrub, make one edit,
export.
**Expect:** if a preview is not ready yet, the message says *what to do*
("производная ещё не подготовлена"), not a bare conflict. Export either produces
a verified file or refuses.
**Red flag:** a green "exported" you cannot play.

### 11. Web Designer
Open a template, run one AI edit, save, reopen.
**Expect:** the edit persists. If the model is unreachable, you get an explicit
"модель недоступна", not a silent no-op.
**Known wart (not a blocker):** the button labelled *"Открыть проект"* has
created a new project instead of opening one. If it still does, note it — it is
recorded as cosmetic and deliberately untouched before your test.

### 12. Apps
**Приложения** — start and stop two or three of the nine.
**Expect:** start/stop works from the screen. There is no environment variable to
set and no restart needed. If control is off, the refusal says so (`403`, "apps
control disabled") and is distinct from "already running" (`409`).
**Red flag:** a `409` on every app with no explanation — that was the old bug.

### 13. Trading Lab
Open it.
**Expect:** either real pipeline state, or an honest **UNWIRED** badge saying the
trading core is not part of this build. It must not crash with a 500.
**Red flag:** a server error, or a confident "ready" badge with no core behind it.

### 14. Browser
Open the browser workspace, navigate to a public page, take a screenshot.
**Expect:** the page loads and the screenshot is stored. Loopback and internal
addresses are refused on purpose — that is a safety rule, not a bug.

### 15. Deliberate failure and recovery
Break something on purpose: put a wrong character in your API key, or block the
network, then run a task.
**Expect:** the run does **not** burn its retry budget re-sending a rejected
credential. It should escalate to you quickly and say what is wrong.
**Record:** how many provider calls it made before asking you.

### 16. A multi-agent task
Give it something that needs more than one step — e.g. *"Research X, then write
a summary file."*
**Expect:** the smallest team that can do the job, visible tool calls, and one
coherent result. **Red flag:** agents duplicating each other's work.

### 17. A local model
**Модели → Найти локальные.**
**Expect:** it scans known local ports (llama.cpp, Ollama, LM Studio, vLLM) and
the disk, and lists what is genuinely there. Attach one and run a short task.
**Red flag:** a model listed that is not actually running.

### 18. Resource pressure
Start something heavy (a local model load, or a video export) while a task runs.
**Expect:** admission fails **closed** when memory is not actually measured — it
must never plan against an invented figure. Owner-facing work should not be
starved by background work.

### 19. Restart and persistence
Stop the server. Start it again. Reopen the app.
**Expect:** tasks, approvals, projects, models and providers are all still there.
An interrupted run resumes or fails honestly — it does not silently vanish or
silently repeat an effect that already happened.

### 20. Export the evidence
Press **publish** on the recording banner.
**Expect:** it reports the number of events and the number of redactions. That
file is the acceptance evidence.

---

## What the run should be able to tell you afterwards

These come out of the recorded session and the task panels — you do not have to
compute them by hand, but they are what the result will be judged on:

| Number | Where it comes from | Target |
|---|---|---|
| review deadlock rate | tasks parked with an empty approvals queue | **0** |
| approvals per successful mission | approvals on the task | one per distinct real effect |
| tokens per verified effect | the task's efficiency panel | thousands, not millions |
| interventions per mission | things you had to do yourself | as close to 0 as possible |
| autonomous completion rate | tasks reaching a terminal state unaided | high |
| dead clicks | recorded automatically | **0** |
| UI console errors | recorded automatically | **0** |
| unexpected 4xx/5xx | recorded automatically, with reasons | only the deliberate ones |
| model fallbacks | when a model was replaced and why | explained, not silent |
| memory pressure | resource samples | measured, never invented |

For comparison, the previous run of this same script recorded: 4 deadlocks,
~121 approvals across 3 tasks, 1 295 189 tokens for one documentation edit, and
4 uses of `/stop`. Those are the numbers this candidate is meant to have moved.

---

## Things already known, so you do not report them twice

* **No liveness URL.** `/health`, `/healthz` and `/api/health` return 404. System
  health is real, but it lives on the **Система** screen (and in the `health`
  block of `/api/system`). Recorded as a convenience gap, deliberately not
  changed before your test.
* **Web Designer "Открыть проект"** may still create a project (step 11).
* **A goal containing a template placeholder** — e.g. *"write a file containing
  `Отчёт <current timestamp>`"* — will fail its content check on the desktop
  operator path, because no real file can contain that literal text. Write goals
  with concrete wording. The failure is now bounded and explained rather than a
  hang, so it is recorded rather than fixed before your test.
* **Cloud agents cannot test this UI** (loopback is blocked by design). Anything
  UI-related must be run by you, locally — which is what this plan is.
* **This branch does not merge cleanly into `main`.** That is a separate,
  pre-existing decision about which gateway ships, and it does not affect
  anything you are testing today.
