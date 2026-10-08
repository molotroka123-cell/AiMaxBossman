# local30: 30 minutes with local models only (07.10.2026, 14:47–15:17 PDT)

Owner rule for this run: local coders do the self-repair, Claude Haiku 5.5 (OpenRouter) only audits,
and a local stand-in for JEV drives the desktop. The orchestrator wrote no product fixes and did no pushes,
no Switch, no owner-root changes and no evo-tree-src ref moves.
Installed backend: :8801, build `6de18f8d9a08` (`/health/live` ALIVE, source_identity PASS).
Raw evidence: `C:\Users\asd\Bossman\bugtest-20261001\tree-1005\selfrepair-local30\cycle01..03`.

## Part A: Winnow-12B as a local JEV stand-in

- Server: `llama-vulkan\llama-server.exe` (build b11223-4da633776), `Winnow-12B-Q8_0.gguf`, `-ngl 99 -c 8192`, port 8091.
  It loaded and answered `/health` with ok after 39 s. The GGUF architecture is supported, with no errors.
- Modalities from `/props`: vision=false, audio=false. Winnow-12B is text-only here because no mmproj file is on disk.
  We took one read-only screenshot of the primary screen (1920x1080) but did NOT send it. The model got a text list of the
  visible top-level windows instead (Settings, ChatGPT, Claude, Edge, Windows Terminal; no Bossman window open).
- Task: "open Bossman Command Center window". The model had to answer with strict JSON `{"action","target","reason"}`.
- With `max_tokens=200` (3 runs): 16.1 / 16.9 / 21.5 s and **empty content** each time. The model is a reasoning model,
  and its `reasoning_content` used up the whole token budget.
- With `max_tokens=600`: 49.2 s, 469 completion tokens, and valid strict JSON:
  `{"action": "launch", "target": "Bossman Command Center", "reason": "...a desktop shortcut is available."}`.
  The target makes sense and the action is harmless. Nothing was clicked or executed.
- Speed: 9.8–13.4 tok/s generation. These runs overlapped with the qwen36 coder from Part B on the same iGPU, so the GPU was shared.
- Bossman wiring (`command-center/bcc/jev/config.py`, `client.py`):
  - The endpoint is configurable through `BOSSMAN_JEV_ENDPOINT` and the model through `BOSSMAN_JEV_MODEL`.
  - The protocol is the TypeSafe `systemone` format, not OpenAI chat. The request is `{model, state, questions}` and the reply
    must be `answers{choice, confidence, probabilities}`.
  - A non-empty `BOSSMAN_JEV_API_KEY` is required.
  - The timeout defaults to 2.5 s with a maximum of 30 s (`BOSSMAN_JEV_TIMEOUT_MS`).
  - Result: Bossman cannot use a local OpenAI-compatible endpoint by configuration alone. That needs a systemone->chat adapter
    (and a dummy key), and Winnow's 16–49 s latency is above the 30 s cap.
  - `bcc/desktop.py` has no model or endpoint setting; it only executes actions.
  - Owner config was not changed.
- llama-server (PID 25432, started by us) was stopped at the end of Part A.

## Part B: self-repair with the local coder

- Model selection: `features/coding_tasks.py` `_LOCAL_CODING_MODELS = ("bossman-fast-qwen36-35b-a3b-q5:latest",)`.
  The sidecar is auto-started against Ollama `http://127.0.0.1:11434/v1`. `TaskIn.model` exists (coding-tasks API), but the cycle
  tool does not send it and it was not needed.
- Model that actually ran (confirmed in the sidecar record): **bossman-fast-qwen36-35b-a3b-q5:latest**, REAL_MODEL, executor bossman-local-sidecar.
- Tool: `tools/tree_self_repair_cycle.py --worker local` (sends worker=None), run with the installed runtime python.
  Every attempt used `--source-repo C:\Users\asd\Bossman\evo-tree-src`. Without it, discovery returns NOT_RUN:
  "add one Bossman checkout to the code roots or choose source_repo".

| # | case | task | steps / tool calls | wall (tool) / task | stage reached | holdout base -> patched | Haiku 5.5 (blind) | truth | Haiku right? |
|---|------|------|-------------------|--------------------|---------------|-------------------------|-------------------|-------|--------------|
| 1 | discovery | 71b52954e98e | 20 / 23 | 383 s / 316 s | INDEPENDENT_VERIFICATION_PASS + EXPERIENCE_AUTO_SAVED | 35/72 fail -> 0/72 fail | ACCEPT (conf 0.6) | PASS | yes |
| 2 | goal-budget | 06723240a604 | 14 / 15 | 597 s / 588 s | FAILED: no patch; sidecar `model_error` | 5/9 fail -> n/a | n/a (no patch) | FAIL | n/a |
| 3 | atomic-json | 86ebe8dda683 | 14 / 15 | 314 s / 262 s | PARTIAL: patch made, own tests green, holdout red | 4/10 fail -> 2/10 fail | REJECT | FAIL | yes |

- #1 caveat: memory recalled and applied 3 earlier `tree-selfrepair-*` recipes for this case. This is a repeat of a known task
  helped by retained experience, not a new solve. Under AGENTS.md it does not count as transfer.
- #2 crash: `HTTPError 500: llama-server returned invalid tool call arguments for "read_file": unexpected end of JSON input`
  (Ollama). The local coder produced a truncated tool-call JSON and the sidecar stopped the run, with no retry or repair.
- #3: the worker's patch adds a short `os.replace` retry (about 200 ms). The holdout cases with a reader holding the file for
  0.4 s and 1.0 s still raise `PermissionError`. Haiku flagged exactly this ("blind retry window of about 200 ms … longer
  contention will still raise PermissionError") before the holdout result was read. Haiku's JSON was cut off at max_tokens; the verdict field was readable.
- Overlap: #3 started while #2 was still running, so both shared the one Ollama model for about 4 minutes.
- Haiku cost: about $0.0009 + $0.0010; latency 6.8 s / 8.1 s.
- Auditor accuracy: 2/2 verdicts matched holdout truth (1 true ACCEPT, 1 true REJECT); n=2 is small.

## Resources (AMD Radeon 8060S iGPU, unified memory, 122.5 GB RAM visible)

- Start: Jeff's `bossman-uncensored-chat` (Q8, 37.0 GB) was resident; 72.8 GB RAM free.
- With qwen36-35b-a3b-q5 (26.9 GB) and Winnow-12B (12.7 GB) loaded: 46.7 GB free. Lowest seen: 38.5 GB free.
- Jeff, Пульт and the backend were not touched. Ollama unloads the coder by itself on keep-alive expiry.

## Assessment for "this PC develops itself without cloud models"

The verdict is in the run report: 1/3 attempts passed the holdout (#1, a repeat of a known task), 0 new solves.
Blockers:
1. Local tool-call JSON is fragile: Ollama returns 500 on a truncated tool call and the sidecar aborts instead of retrying.
2. Patches only cover the shallow part of the bug (#3: green self-tests, red holdout). No local verifier closes that gap;
   so far only the cloud auditor caught it.
3. There is no local desktop operator path: the Jev client speaks systemone, not OpenAI; the local model is text-only;
   and its 16–49 s latency is too slow for the Jev timeout.
