# UI-control candidates and capture backends (checked 2026-10-06)

Method: web search/fetch from the build container. Hugging Face, OpenRouter, MDN and Microsoft Learn were **blocked** by the egress policy, so facts marked *snippet* come from search snippets only and every unverifiable item is `UNVERIFIED`. Nothing below was run on a GPU or on Windows.

## 1. Cygnet, Winnow-12B, Jev — are they UI-control models?

| item | what the sources say | source | status |
|---|---|---|---|
| JevBench | benchmark for "Jev-class decision models": routing, answer adequacy, policy checks, intent classification, ordinal scoring, enum extraction; axes Intelligence / Calibration / Speed / Cost. **Text only; not a GUI-grounding benchmark.** Harness MIT; other parts have own licences (THIRD-PARTY.md). Version mismatch: README v1.4.2.2, leaderboard v1.5.4 | github.com/fstandhartinger/jevbench; benchmarkheaven.com/jev-models/v1.5.4 | read (README) / snippet |
| Cygnet | `blockbrain-ai/cygnet-recipe`: frozen `google/gemma-4-12B-it` behind stock vLLM 0.30.0 with a one-token option-letter readout; input = JSON state + instructions + lettered options, output = option probabilities. Code MIT; weights Apache-2.0 per the repo (+ Gemma prohibited-use policy). Measured on RTX A6000 48 GB and L40S 48 GB, context 16 384; Linux/Docker only | github.com/blockbrain-ai/cygnet-recipe | read |
| Winnow-12B | `eldanring/winnow-12b`: LoRA fine-tune of Gemma 4 12B IT, merged; GGUF `Winnow-12B-Q8_0.gguf` 12.67 GB, BF16 23.83 GB, llama.cpp; `/v1/systemone` typed decisions + chat completions. Weights/code **licence not verified**; Windows/Vulkan/ROCm **UNVERIFIED**; the 12.7 GB file implies ≳ 13 GB RAM/VRAM plus KV cache (inference, not measured) | huggingface.co/eldanring/winnow-12b | snippet only |
| Jev / `typesafe/jev-router` | hosted "System One" typed-decision router on OpenRouter (listed 2026-09-25, text/image/audio/video/file inputs, text output). Not open weights; licence **UNVERIFIED** | openrouter.ai/typesafe/jev-router | snippet only |

**Finding.** All three are *typed-decision* models (state + options → chosen option letter + probabilities). They take no screenshot-to-coordinate task and return no click boxes, and the JevBench numbers on the owner's screenshot measure routing/classification, **not** poker and **not** UI grounding. They are therefore **not candidates for locating a button**. Where they could help is *choosing among already-detected candidates* ("which of these 3 boxes is the CALL button?"); our vision locator already answers that deterministically with 0 wrong clicks in the bench below, so there is no measured reason to add a 12 B model to the click path.

Status: **NOT_RUN** (no weights reachable, no GPU, not a grounding model). They are *not* rejected forever: if the owner runs a typed-decision model locally, it plugs in through the same `ModelLocator` guard (below), and `eval/ui_bench.py` can run it on the same scenarios once a small `GroundingClient` for it is written (not written: nothing to run it against here).

## 2. What a real grounding model would have to pass here
A UI-grounding VLM (screenshot + "click CALL" → box) would be plugged in as `ModelLocator(client)`. Guard (unit-tested): same label; box inside the frame; IoU ≥ 0.30 with the button vision detects; otherwise refused. Selection rule: **by our scenario results** (`evidence/REPORT_COACH_EXECUTOR.md`): wrong clicks must be 0, then success rate, then latency and memory. No such model could be downloaded or run in this container, so there is no measured comparison with one.

## 3. Capture backends (existing vs Windows Graphics Capture)

| | python-mss 10.2.0 (existing `ScreenWindowSource`) | windows-capture 2.0.1 (WGC, Rust) | dxcam | browser `getDisplayMedia` |
|---|---|---|---|---|
| licence | MIT | MIT | MIT | web API |
| per-window capture | **no**: screen region only | yes: documented by window **title** (`window_name`); `window_hwnd` **UNVERIFIED** (check the source) | no (region/monitor) | user-picked window/screen |
| what you get when another window covers it | whatever is on screen (the cover) | WGC captures the window's own content — expected, **UNVERIFIED** | desktop duplication = composited screen | the surface the user picked |
| minimised window | no | **UNVERIFIED** | no | n/a |
| detects window close | no | yes, `on_closed()` | no | track `ended` |
| exposes pid / handle / rect | no | no (title only) | no | **no** (only `displaySurface`: browser/window/monitor) |
| GPU/AMD | CPU only, vendor independent | Windows graphics stack; vendor independence **UNVERIFIED** | DXGI; docs only mention NVIDIA numbers | n/a |
| run here | **NOT_RUN** (no display) | **NOT_RUN** (Windows only) | NOT_RUN | NOT_RUN |

Consequences implemented:
* Identity is **not** taken from the capture API. The backend binds a source to `WindowIdentity(handle, pid, process start time, title)` read from the OS (`control/identity.py::WindowsProbe`, Win32 via ctypes — **NOT_RUN**, reviewed only) and re-checks it on every frame and every click. Title is for humans only.
* The picker in Bossman is backed by the existing capture backend and shows its stream as MJPEG; the browser's own `getDisplayMedia` was not used because it exposes no pid/handle/rect, so a click could not be tied to the process.
* Capturing through region (mss) shows covering windows, hence the occlusion check; windows-capture would fix that for *observation* but is not needed for the executor's decisions, which stop on occlusion anyway.
* Decision: keep the existing backend now (it is the only one that can be exercised here); evaluate `windows-capture` on the owner's Windows PC as a *second* capture implementation behind the same `Frame` + `WindowProbe` contract. Selection will follow the occlusion/minimise/close scenarios of the bench run on that PC.
