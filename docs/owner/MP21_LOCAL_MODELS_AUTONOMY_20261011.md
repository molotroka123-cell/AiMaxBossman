# MP21 Track A, section 3: local models, offline mode, queue and recovery, computer control

Measured 11.10.2026 on THIS machine, read-only. Nothing was downloaded, loaded or unloaded by this pass
(the one smoke chat used a model that was already resident).

## 1. Hardware facts

| Item | Value | How measured |
|---|---|---|
| CPU | AMD RYZEN AI MAX+ 395 w/ Radeon 8060S, 16 cores | `Win32_Processor` |
| RAM | 119.6 GiB physical, unified with the GPU | `Win32_ComputerSystem` |
| GPU | exactly one: AMD Radeon 8060S Graphics (gfx1151), driver 32.0.22021.1009 | `Win32_VideoController`, Ollama server log |
| GPU memory seen by the ROCm runtime | 81,703 MiB (about 79.8 GiB) of the shared pool; 49.3 GiB reported free at 02:29 with two models resident | `llama-server` / Ollama log lines `found 1 ROCm devices` and `gpu memory ... available` |
| Vulkan | 1 device, UMA, coopmat, `llama-vulkan\bench-vulkan.log`: gemma3 27B Q8_0 pp64 220.8 t/s, tg32 6.47 t/s | `C:\Users\asd\Bossman\llama-vulkan\bench-vulkan.log` |
| NVIDIA / CUDA | no `nvidia-smi`; the `cuda_v12`/`cuda_v13` directories under Ollama's `lib` are bundled libraries, not hardware | `Get-Command`, directory listing |
| Disk C: | about 382 GiB free (1.49 TiB used) | `Get-PSDrive` |

Multi-card section: this machine has ONE GPU. There is no second card, no NCCL, no PCIe peer link, no tensor or
pipeline sharding, and the "several RTX 3080" cluster discussed earlier is not installed here. Nothing in this
document adds VRAM across cards. The only distributed-capable pieces present are `ggml-rpc.dll` and
`ggml-rpc-server.exe` in `llama-vulkan` (llama.cpp RPC backend); no remote RPC host is configured, so
distributed inference is NOT demonstrated. A mismatch to fix in the docs: `docs/hardware/LOCAL_HARDWARE_INVENTORY.md`
(2026-08-31) describes a different laptop (i9-14900HX, RTX 4060 8 GB, 15.6 GiB RAM) and must not be read as this machine.
`docs/hardware/AI_MAX_PRO_128GB_AUDIT.md` and `LOCAL_MODEL_STRIX_HALO_2026-09-07.md` are the matching ones, and the
latter says its benchmark numbers are provisional until reproduced.

## 2. Runtimes

| Runtime | Version / location | State |
|---|---|---|
| Ollama | 0.40.2, server on 127.0.0.1:11434 (pid 16868), runner `llamacpp` | running |
| llama-server (Ollama's bundled, ROCm) | `AppData\Local\Programs\Ollama\lib\ollama\llama-server.exe`, launched with `--offline --host 127.0.0.1 --no-webui` | 3 processes resident (pids 3584, 14148, 32216) |
| llama.cpp Vulkan build | `C:\Users\asd\Bossman\llama-vulkan` (b11223, zip kept) | present, not running |
| Bossman backend | installed bundle `app\BOSSMAN-Windows-x64-6de18f8d9a08\runtime\python.exe -m bcc --port 8801`; a second dev backend on :8871 | running |
| OpenAI-compatible adapter | `bcc/providers.py::OpenAICompatAdapter` | used by the smoke |

## 3. Installed models

Ollama (`ollama list`, 11 entries, 37 blobs, 141.3 GB on disk):

| Name | Size | Architecture / quant | Licence text embedded in the model | Resident now |
|---|---|---|---|---|
| bossman-fast-qwen36-35b-a3b-q5 | 26 GB | qwen35moe 34.7B, Q5_K_M, ctx 32768 | none embedded | yes (GPU 100 percent, keep-alive forever) |
| ace-decider-08b | 811 MB | qwen35 0.75B, Q8_0 | none embedded | yes |
| bossman-main-qwen38-27b-q5 | 19 GB | qwen35 27.3B, Q5_K_M | none embedded | no |
| bossman-fast-qwen36-vision | 27 GB | (vision build) | none embedded | no |
| bossman-uncensored-chat | 36 GB | | none embedded | no |
| bossman-community-qwen-uncensored | 36 GB | | none embedded | no |
| qwen3-coder:30b | 18 GB | qwen3moe 30.5B, Q4_K_M | Apache 2.0 | no |
| qwen38-pi-ab | 17 GB | | Apache 2.0 | no |
| hf.co/bytkim/Qwen3.8-27B-pi-GGUF:Q4_K_M | 17 GB | | Apache 2.0 | no |
| gemma3-27b-abl-test | 28 GB | | none embedded (Gemma terms apply upstream) | no |
| ace-qwen3-17b | 2.2 GB | | none embedded | no |

Weights on disk outside Ollama (`C:\Users\asd\Bossman\models`, about 777 GiB in 20 folders, no LICENSE file in any of
them): embedding (bge-m3, Qwen3-Embedding-0.6B), reranker, router (decider-0.8b, Clef-Flash, Qwen3-1.7B), voice,
llm (Winnow-12B Q8), qwen36-35b-a3b (UD-Q5_K_M + mmproj), qwen38-27b (UD-Q4/Q5 + mmproj), qwen38-flash-next
(IQ3_XXS, 3 shards, 76 GB), gpt-oss-120b (MXFP4, 59 GB), llama33-70b-abliterated (Q8_0), r1-70b-abliterated (Q4_K_M),
deepseek-v4-flash-abliterated (Q2, 80.8 GB), gemma3-27b / gemma4-31b abliterated or uncensored, nex-n25-mini,
occamy-1.0, media (Wan2.1/2.2, Qwen-Image, Flux, Z-Image GGUFs, 158 GiB), genjutsu.

Licence status: only 3 of 11 Ollama models carry licence text; the `models` folders carry none. Licences of the rest
are UNVERIFIED locally. Several are third-party "abliterated/uncensored" re-quantisations whose terms follow the
upstream base model; that has to be checked at the source before any commercial use. No claim is made here.

Practical envelope: a Q5 27B model plus a 35B-A3B MoE plus the 0.8B decider are resident together at 49 GiB
free headroom; gpt-oss-120b (59 GB) fits alone; the 70B Q8 (70 GB) and deepseek-v4 Q2 (81 GB) are at or beyond the
visible 79.8 GiB pool and are not expected to run together with anything else. These are size arithmetic, not new
benchmarks; the only measured speed in the repo for this GPU is the Vulkan gemma3 27B Q8 figure above.

## 4. Offline and local-only mode

| Piece | Where | What it really does | Status |
|---|---|---|---|
| `BOSSMAN_OFFLINE_MODE_ENABLED` + `GET /api/offline`, `/api/offline/can/{capability}` | `bcc/features/offline_mode.py` | reports which capabilities need the network; tri-state offline_ok / needs_network / unknown; probes loopback with `trust_env=False`. Does NOT block anything | works (test_offline_mode, in the 21-test run) |
| per-request privacy `private`/`local_only` | `bossman_shared/privacy.py`, `engine.py` | blocks cloud providers for that request; engine turns the refusal into a visible `privacy: ...` failure | works (existing tests) |
| `local_chat_only` in Pit | `bcc/pit/config.py`, `runtime.py` | Jeff chat uses one configured local model only | partial (not exercised in this pass) |
| `BOSSMAN_LOCAL_ONLY=1` (NEW, this pass) | `bossman_shared/privacy.py::local_only_switch` | process-wide: `assert_provider_egress` refuses openrouter/anthropic/openai/gemini kinds and every non-loopback, non-private host, for every request, without a restart | works for the provider boundary (21 tests + smoke rc=0) |

Gap that existed at base `250e24ec`: the offline flag only labelled runs `OFFLINE` in `run_provenance`; with no per-request
mark every cloud provider stayed reachable, so there was no single switch for "no silent fallback to cloud". Fixed by
the new switch (defect repro: 10 test errors before the change, 21 passed after). `GET /api/offline` now returns
`local_only_enforced` and `enforce_flag`, and the provenance label is `OFFLINE` when either flag is set.

Remaining holes, stated so nobody reads "offline" as total: web research, browser navigation, Gmail, Telegram
companion/calls, OpenRouter catalog refresh and model downloads do not go through `assert_provider_egress`. The
switch does not stop them; `/api/offline` lists them as `needs_network`. Setting `BOSSMAN_LOCAL_ONLY` also needs to
be exported to the installed backend's environment before it starts (the running :8801 process has not been
restarted and does not have it).

Offline smoke (command and rc in `docs/owner/runs/MP21_A_CHECKS_20261011.md`, row 6): `BOSSMAN_LOCAL_ONLY=1 python
tools/offline_smoke_local_only.py`, rc 0, 0 external connects, one local chat answered in 2.9 s.

## 5. Queue, cancel, recovery, journal

Evidence is the 98-test run (row 1 of the checks file, rc 0). No live restart of the :8801 backend was done.

| Capability | Code | Test evidence | Status |
|---|---|---|---|
| Queue with retry | `bcc/engine.py`, `bcc/scheduler.py` | `test_queue_retry.py` | works |
| Jeff queue survives restart | `bcc/pit/*` | `test_jeff_queue_survival.py` | works |
| Cancel in flight, fence events | `bcc/engine.py` | `test_fence_event_cancellation.py` | works |
| Model recovery and fallback with an owner-readable failure | `engine.py` (PermissionError / ProviderError branches), `model_health.py` | `test_rc19_model_recovery.py`, `test_offline_mode.py` | works |
| Autonomy journal (append, no secrets) | `bcc/autonomy/journal.py` | `test_autonomy_journal.py` | works |
| 24 h unattended run | see `docs/autonomy/BOSSMAN_2_1_AUTONOMOUS_COMPUTER_OPERATOR.md` ("not recorded") | none | missing |

## 6. Computer control loop

| Step | Implementation | Evidence | Status |
|---|---|---|---|
| Screenshot | `tools_computer.observe(..., screenshot=True)`; `bossman-core/.../adapters/screenshot.py` | `test_computer_use_tools.py` | works (fakes; real desktop not exercised here) |
| UI elements | UIA tree per window, `_element_with_rect`, `window_allowlisted`, per-task app scope (HWND/PID bound) | `test_computer_use_tools.py`, `test_computer_verify_and_approval.py` | works (fakes) |
| Mouse / keyboard | `adapters/windows.py` via pyautogui with `FAILSAFE=True`, hotkey and typing budgets, credential-field refusal | same tests | works (fakes) |
| Verify after action | `tools_computer.verify(obs, expect)`; a launch counts only with a verified window | `test_action_contract_verified_computer_launch.py` | works |
| Owner hand-over | `POST /api/computer/stop` then `POST /api/computer/resume`; resume invalidates queued actions and demands a fresh observation; STOP is a file under `data_dir/computer`, so it survives a restart | `test_computer_stop_race.py` (10 tests incl. `stop_survives_restart`, `stop_and_resume_are_not_model_callable`) | works |
| Emergency stop | same STOP file and event, interrupts in-flight waits and sub-steps; panel button in `ui/pages/control.js`; pyautogui corner fail-safe | same tests | works |
| Global hotkey kill | no `RegisterHotKey` or keyboard hook anywhere in the repo | grep | missing (the owner must reach the panel or the pointer corner) |
| Autonomous multi-site mission (browser, download, process) | design only per the 2.1 operator doc | none | missing / not proven live |

Real-desktop drive was NOT run: the video renders have priority on this machine and a live click loop would take the
mouse and keyboard. The 98 tests above substitute fakes for the Windows adapter. This is the exact next step:
run `tools/cu_acceptance` on a quiet desktop, with the owner present, and keep its junit output.
