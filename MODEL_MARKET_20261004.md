# Bossman local model market — 2026-10-04

## Decision

**Tournament complete; no PRIMARY promotion.** The isolated Bossman CMD/API final round (15 tasks × 2 repeats per finalist) selected Qwen3.6-35B-A3B Q5 at 82.307%, ahead of Qwen3.8-27B Q5 at 72.213% (+10.094 percentage points). Both passed STOP, Browser, recovery, and long-context checks; each missed one tool assertion. Qwen3.6 passed 8/12 hidden coding checks versus 6/12 for Qwen3.8. A short round had Qwen3.8 and Qwen3.6 tied at 9/10; Ornith scored 7/10. Muse did not complete its short round (only one of four coding attempts yielded an applicable patch; two requests failed), so it is incomplete and unranked. These candidate outcomes and limitations are recorded under `quick_round` and `final_tournament` in `results.json`.

### Tournament evidence audit

The saved finalist records contain 30 completed Bossman task results per model, prompt hashes, timestamps, task/run IDs, model aliases, checkpoints, and scrubbed responses. Recomputing the scorecard from those files reproduces the same 82.307% versus 72.213% result. However, both run records have `runner_sha: null`. The checked-in `run_final_tournament.py` was committed at 04:57 local time, after the saved finalist runs began at 03:40 and 03:50 local time; its hash therefore cannot be asserted as the exact runner used. The private isolated BCC database and credentials are excluded, so the API run IDs cannot be independently reconciled from the committed artifacts alone. Treat the local comparison as strong saved-run evidence, not a fully pinned/replayable A-grade benchmark. Do not claim the runner or server request path was independently audited from source captured at run time.

### Isolated owner-stack validation (2026-10-04)

Qwen3.6-35B-A3B Q5 ran through a separate Bossman source-checkout API on loopback with its own data directory. It completed exact-answer tasks before and after a Bossman restart and after a llama-server restart. With Qwen3.6 intentionally stopped, Bossman completed a task through the Qwen3.8 Q5 fallback; after Qwen3.6 restarted and Qwen3.8 was stopped, Qwen3.6 completed a primary-route task. A safe `memory.write` task created a note inside the isolated test vault after that vault was configured; the STOP task returned a refusal with zero tool calls. All test servers were stopped and the test ports were closed afterward.

Limits and risks: this was source-checkout evidence, not the installed Windows artifact. Bossman readiness was `true`, while health reported `DEGRADED` because an optional component was unknown. An initial memory-tool attempt failed because the test vault had not yet been configured; the model replied `DONE` despite the task being marked failed and no note being written. That attempt was excluded from PASS; the same safe task passed after the isolated vault was configured. The owner routing-config fingerprint at final inspection (`f0f10a72814dab2dde39128776eb761d66b71dab250e5f8cabd712203a336cc2`) differs from the value captured at the start of this audit (`f0f10a72814dababd39128776eb761d66b71dab250e5f8cabd712203a336cc2`). This run did not write that file, so the mismatch is unresolved and must be reconciled before any production promotion. Full owner-product, worker orchestration, Jeff, UX, Telegram, heartbeat, and Windows Computer Use acceptance remain unverified.

The tournament win alone does not satisfy the promotion gate. A later isolated source-checkout validation proves Qwen3.6 answers Bossman CMD/API tasks across Bossman and llama-server restarts, writes to an explicitly isolated memory vault, obeys a STOP prompt, and succeeds as primary/fallback in a two-model failover exercise. It does not validate the installed owner product or the full PRIMARY→WORKER→fallback workflow. Integrated Jeff/CMD/UX/Telegram polling/heartbeat/Browser/Computer Use checks were not run. Keep **PRIMARY = Qwen3.8-27B Q5**, **LOCAL_WORKER = none**, and `MODEL_STACK_OWNER_PROVEN=NO`. Qwen3.6 remains the tournament winner and next candidate for owner acceptance; it is not deployed. See `benchmarks/model-market-20261004/owner-stack-validation.json` for the bounded test record. The previously supplied Qwen3.8-pi result (0/4, no code change) remains owner-reported and unverified. One diagnostic rerun was attempted here but produced no usable output and was stopped; it adds no score.

## Captured owner-PC state

- `SOURCE_SHA`: `e76de12b239dc977e0170bdb5c97f1bd084b90f9` (source repo `C:\Users\asd\Bossman\src`).
- Branch/worktree: `research/model-market-20261004`, `C:\Users\asd\Bossman\worktrees\model-market-20261004`, clean at start and based on SOURCE_SHA.
- Original source checkout had pre-existing edits in `command-center/bcc/engine.py`, `command-center/bcc/providers.py`, `command-center/tests/test_local_text_tool_bridge.py`, `docs/agent/*`, and `owner-audit/glm53/CP-06.md`. Left intact.
- Host: Windows, AMD Ryzen AI Max+ 395, Radeon 8060S, 128 GB unified memory reported by owner. Windows currently reports 119.6 GiB visible and 62.1 GiB free at capture; free C: space 375.2 GB.
- Live processes: Ollama 0.35.1, `ollama serve` PID 17432 on 127.0.0.1:11434; Ollama `llama-server` PID 20368 on 127.0.0.1:57389, ~31.5 GB working set. `GET /api/ps` reports `bossman-community-qwen-uncensored:latest`, Q8_0 34.7B, context 32768, ~34.8 GB resident. Also active Bossman PIT CLI PID 15288 and Telegram companion PID 2408.
- Ollama metadata for `qwen38-ab-baseline:latest`: GGUF, Qwen3.8-27B (27.32B), Q5_K_M, Apache-2.0, official chat template, 262144 model context; current resident model is a separate Qwen-derived 34.7B Q8_0 model. `qwen38-pi-ab:latest` exists locally; no rerun performed.
- `start-models.ps1` documents llama.cpp Vulkan build b10964, Qwen3.8-27B Q5_K_M as `main` on :8081 with mmproj and Qwen3.6-35B-A3B Q5_K_M as `fast` on :8082. The script is not the currently running Ollama process, so its version/backend is not proof of the current API backend.
- Config fingerprints at capture: `start-models.ps1` SHA256 `599224a717f1b5f41fa2f45e8743cc770d0b64b316bb2a937e8eea5ebd23c559`; installed `model-routing-stack.json` SHA256 `f0f10a72814dababd39128776eb761d66b71dab250e5f8cabd712203a336cc2`.
- `ollama ps` reported the resident model as 100% GPU offload. `rocminfo` and `hipconfig` were not on PATH; only the Windows `clinfo.exe` was found. This confirms Ollama's current placement report, not which compute backend implements it.
- No Bossman Command Center listener was present on its launcher’s configured :8800 at capture. Starting it would create a new owner service against the owner data directory. No service was started, no model was loaded, and no stateful CMD/API evaluation was run.
- Rollback: no canonical files/configuration/processes/models were changed. The original checkout and its dirty files remain untouched. Revert this evidence-only commit or delete its branch/worktree after review to roll back the research artifact.

## Scoring of evidence

- **A**: exact candidate and revision, executable public benchmark, pinned harness/data/config, open item-level logs/results.
- **B**: official result with meaningful harness/settings described, but no open raw logs or no local reproduction.
- **C**: model-card benchmark claims without enough harness detail to compare.
- **D**: marketing, repository name, anecdote, or no reproducible result.
- A/B means *benchmark evidence*, not that a model passed Bossman. No candidate has an A-grade Bossman run here. The supplied Qwen Q5 and pi outcomes are recorded as **owner-reported** because raw Bossman logs were not attached to this run.

## Candidate cards

Per-candidate best located public benchmark, source grade, and local Bossman outcome crosswalk: [PUBLIC_BENCHMARK_CROSSWALK_20261004.md](PUBLIC_BENCHMARK_CROSSWALK_20261004.md). Public scores are for discovery and context only; the local tournament below is the selection evidence.

Dates and sizes are upstream-reported or rough quantization estimates where marked. `n/a` means not found in the cited upstream record, rather than inferred. Recent means a repository/model update or release between 2026-08-05 and 2026-10-04 inclusive. The two downloaded candidate revisions and actual Ollama blob digests are recorded below; other mutable refs remain discovery-only.

| # | Candidate / repo and rev | Date, architecture, active/total, quant, size | Context, vision, tools/template, license | AMD/runtime and RAM estimate | Results and grade | Disposition |
|---|---|---|---|---|---|---|
| 1 | Baseline Qwen/Qwen3.8-27B, local `qwen38-ab-baseline:latest` (digest not pinned in saved card) | Official 2026-08 release; dense Qwen3_5, 27.32B; Q5_K_M, 19 GB local | 262K model card; image+video; native Qwen tool format/template; Apache-2.0 | Ollama GGUF currently supports this loaded architecture; ~20–30 GB plus KV, comfortable alone | Official card has broad reported coding/agent benchmarks (C); owner says 4/4, 4 turns/3 tool calls (unverified here) | Keep PRIMARY; ranked baseline in the completed isolated Bossman tournament |
| 2 | Baseline Qwen/Qwen3.8-27B, Q6_K / Q8_0 GGUF | Same date/arch; 27.32B; estimated 23–25 GB / 29–31 GB | Same as #1; Apache-2.0 | GGUF likely compatible; ~25–42 GB including practical KV | No separate agentic result for these quant levels (D for quant-specific claim) | Eligible only for later smoke; no reason to consume disk now |
| 3 | PaoAI/GLM-5.3-Flash-PaoAI-ROCmFP4-STRIX-BALANCED-GGUF (main; pin before use) | Recent community quant; GLM MoE ~320B total/~A24B; ROCmFP4; repository size not safely verified | Very long context in base card; multimodal claims vary; GLM template; upstream GLM license | Custom ROCmFP4 fork/format; label “Strix” is not compatibility proof. Likely >88 GB; no RAM estimate trusted | No model-specific open Bossman logs; community throughput/report claims only (D) | Exclude from download/test until exact file proves ≤88 GB and backend support |
| 4 | TeichAI/Qwen3.8-27B-Fable-Distill, HF main | Updated within scan window; dense Qwen 27B BF16 ~55 GB, GGUF derivatives vary | Base Qwen context/vision/template may change after tune; Apache base, derivative terms verify | GGUF derivatives may work; estimate quant-specific, not pinned | Card reports ARC/BoolQ, private traces; no relevant executable agent metric (C/D) | Do not test on name/card claims |
| 5 | khazarai/Qwen3.8-27B-Fable-5-Coding-Distilled, HF commit `615adddb64f5f0c28d53c581536cb68e040e1f44`; GGUF derivative exists | Recent card; dense 27B; BF16 source ~55 GB; GGUF quant sizes need exact file pin | Qwen image/video processor; tool syntax inherited/modified; Apache-2.0 | Potential GGUF path, but exact AMD smoke absent; Q4/Q5 ~17–22 GB weights plus KV | Training data/traces are described; no reproducible independent SWE/Terminal/BFCL/tau2 score found (D) | Exclude pending benchmark evidence |
| 6 | vwdubb/Swift-Qwen3.8-27b-Terse-Coder, HF main (`a45230c` config commit) | Current card recent; dense 27B; source BF16 ~55 GB, quant not pinned | Qwen3_5; 262K base; vision; Swift Open License 1.0 | Only candidate quant/runtime can qualify; rough Q5 ~20 GB + KV | No reproducible named benchmark located (D) | Exclude pending evidence and exact GGUF |
| 7 | Swift-Qwen3.8 agent/coding family; repo/revision ambiguous from search results | Recent community derivatives found, but no single canonical revision identified | Architecture/template/license vary by derivative | Not estimable before canonical repo is named | No qualifying reproducible result located (D) | Do not select on family label |
| 8 | Qwen3.8 code-analysis MTP community variants | Several MTP/head/ROCm experiments surfaced; exact repo not pinned | Base Qwen 27B plus drafter/head; quant and template are build-specific | ROCmFP4 custom build may be AMD-specific; memory unknown | Anecdotal speed or accuracy posts; not independent executable agent eval (D) | Exclude pending exact model and reproducible results |
| 9 | Other recent Qwen3.8 fine-tunes incl. Cold-Fusion/Heretic/abliterated/merged | Repositories vary; sample surfaced `DavidAU/Qwen3.8-27B-TURBO-Fable-Cold-Fusion-735-882-Heretic-Uncensored-NM-DAU` and quant-only NVFP4 derivatives | Qwen 27B derivatives, revision-specific | Quant-only formats may be NVIDIA-specific; GGUF sizes unknown by pinned revision | Naming/README claims without neutral executable logs; provenance/safety concerns (D) | Exclude absent complete provenance, license, template, reproducible score, safety pass |
| 10 | Qwen3.6-35B-A3B official and local Q5 candidate | Official family predates window per surfaced records; MoE 35B total/3B active; local Q5 GGUF 26 GB | Long-context multimodal; Qwen tool template; Apache-2.0 | Already documented in owner launcher; ~28–40 GB + KV, AMD path exists locally | No current exact-revision Bossman comparison in this run; official model-card claims are not a same-harness result (C) | Tested challenger; won the final round, but owner-stack promotion gates remain open |
| 11 | Qwen3-Coder-Next (official/community GGUF; local `qwen3-coder-next:q4_K_M`) | Release outside last-60-day scan; MoE coding model; Ollama tag 51 GB | Long code context; text; Qwen tool format; license per exact base card | Ollama can store/run installed tag; anticipated >55 GB plus KV, leaves <88 GB target margin when Bossman stays loaded | Official coding benchmark claims, but no new same-stack evaluation (C) | Installed, but skip: older and resource-heavy for small expected delta |
| 12 | ornith-ai/Ornith-1.0-35B-GGUF @ `383064f72a1ef3087b779f268d3ca117eb989aac` (last modified 2026-07-18; outside 60 days, but user explicitly required review) | Qwen35MoE, 34.7B total; Q4_K_M 21 GB, local content SHA256 `ff25291b2599fb927a835e624d2b3540106af61761c3fa57ac4264046dbec002` | 262K advertised; text/tool use; Qwen-family Jinja template; MIT | llama.cpp b10964 Vulkan loaded; Bossman exact-answer task passed; fit observed with 30 GiB free | Official Terminal-Bench 2.1 (5 runs), SWE-bench Verified/Pro, ClawEval harness details (B); local route smoke only | Mandatory longlist item; quick round 7/10 (hidden coding 1/4); no final-round slot |
| 13 | meta-models/Muse-Glimmer-30B-GGUF @ `70bf1b61ac09f91b24d39038091b41c582bc5d7a` (last modified 2026-08-18) | 27.9B text tower + 1.9B vision projector; Q4_K_M 16 GB + projector 1.4 GB; local SHA256 `4cc57c0f51040a226e5a72cc47b7613f7772950e460a665f7083de89f183f60e`; projector SHA256 `f48b452316f9b213758e8659444029b961a24a07f99a1abb2a9f88b06f7c00c6` | 131K advertised; text/image and tools; GGUF Jinja; Apache-2.0 | llama.cpp b10964 Vulkan loaded both shards; Bossman exact-answer task passed; 31.5 GiB free while loaded | Meta reports SWE-bench Verified, Terminal-Bench 2.1 and agentic scores with described harness (B/C); local smoke only | Quick round incomplete after two failed requests; 1/4 coding checks; unranked and not eligible for LOCAL_WORKER |
| 14 | Cloudflare/clef-flash (HF main `17f0b0a`) | Added within window; Qwen3.5-9B base; 9B; FP/BF16 weights 19.1 GB per repository tree incl schema head | Text/image; custom joint schema head and custom `systemone` interface; see repository template; license in LICENSE | Community GGUF exists but custom head may be lost; base may fit at ~8–12 GB quant | Decision Index and schema claims, benchmark method not pinned here (C) | Do not test GGUF until custom head parity validated |
| 15 | autotrust/JEV-27B-VL (main) | Published 2026-10-01; Qwen3.8-27B base, dense 27B; quant not specified | Vision; System 1 `/v1/decide` + System 2 Qwen; license check exact repo | Author's serving instruction asks for one 80GB+ GPU and vLLM; unsuitable for current Windows ROCm path absent proof | Zero-shot CLINC150 claims and custom decision metric, not tool-agent benchmark (C) | Exclude: runtime/format mismatch |
| 16 | mistralai/Devstral-Small-2507 / “Small 1.1 24B” | 2025-07; dense 24B, newer model card may represent updated release; outside scan window | Coding, tool-call support, text; Mistral license | GGUF may run; 15–20 GB Q4 plus KV | Official SWE-bench result, OpenHands harness described, historical (B) | Mandatory longlist, exclude by 60-day rule; fallback only if window relaxed |
| 17 | Google Gemma 4 12B FABLE/Composer/tau2 derivatives (`yuxinlu1/gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF`) | Community repo updated Jun-2026; official Gemma4-12B released Jul-2026; outside 60 days | Dense 11.95B; multimodal text/image/audio; Gemma template; Apache-2.0 base | Official GGUF available; Q4 roughly 7–10 GB + KV; AMD feasible through llama.cpp once exact build smoke-tested | Tau2 multiplier in name is not benchmark proof; official function calling docs (C), repo name alone D | Exclude by date/evidence; no endorsement of claimed 3.5x |
| 18 | openai/gpt-oss-20b and GGUF | 2025-08; MoE 21B total/3.6B active; MXFP4 official weights, size ~13.8 GB | Text, Harmony format, tools; Apache-2.0 | AMD Developer Cloud and Ollama/llama.cpp quant ecosystem; ~15–20 GB + KV | Official tool-use support and eval card (B/C); not recent | Mandatory longlist; exclude by 60-day rule |
| 19 | NVIDIA Nemotron Nano/Super: Nano 12B-v2-VL-NVFP4-QAD; Super/3.5 Lightning family | Nano v2 is 2025; Super large models exceed 88GB unless heavy quant; Nemotron 3.5 Lightning 30B-A3B NVFP4 updated Aug-2026 | Nano 12B dense/hybrid; Lightning 30B/3B active MoE; vision varies; model-specific templates; NVIDIA Open Model License or OpenMDW-1.1 | NVFP4 CUDA/TRT/vLLM is not AMD-compatible by default. NVIDIA publishes a local GGUF route for Lightning, but validate AMD support before test; NVFP4 file 21.6 GB (not evidence of GGUF format) | Lightning official recipe names NeMo Gym/NeMo Evaluator, Terminal-Bench/SWE-Bench; strong B. Nano older and NVIDIA-optimized | Lightning is conditional AMD smoke candidate; other Nano/Super entries exclude by age, format, or 88GB limit |
| 20 | Experimental Heretic, Cold-Fusion, abliterated, merged | Repo-specific; sample variants found for Qwen and Gemma | Architecture, ancestry, template and license must be individually pinned | Cannot infer from names; some NVFP4 files not AMD runnable | D unless public harness, raw results, full lineage and safety are supplied | Exclude from executable tournament by default |
| 21 | Qwen3.8-Flash-Next, DeepSeek-V4-Flash and GLM-5.3-Flash larger MoEs (additional families) | Recent official/community releases; total sizes 170–760B for some releases | MoE and multimodal/template details vary | Candidate quant reports include >88GB (GLM base NVFP4 ~169GB, Qwen Flash Next ~120B-parameter NVFP4); most exceed desired model budget. User explicitly bars MiMo/Kimi >150GB and these large variants have no reason to bypass that gate | Official or community benchmark claims vary; not relevant before resource/runtime pass | Exclude large checkpoints before download; do not try to fit by paging |
| 22 | Colibri P3 | Repository/checkpoint not pinned in this run | Unknown | Explicitly barred by owner; no download or test | No evidence assessed | Excluded as instructed |

## Tournament result (2026-10-04)

Both finalists ran through the isolated Bossman CMD/API route with the pinned local runtime and the same fixed task configuration. The final round is 15 tasks × 2 runs per model; the coding subset was additionally scored against hidden executable tests. These are local tournament results, separate from third-party leaderboards.

| Metric | Qwen3.6-35B-A3B Q5 | Qwen3.8-27B Q5 |
|---|---:|---:|
| Hidden coding tests | 8/12 | 6/12 |
| Coding patches applied | 11/12 | 8/12 |
| Tool assertions | 5/6 | 5/6 |
| Browser tasks | 4/4 | 4/4 |
| Recovery tasks | 2/2 | 2/2 |
| Long-context tasks | 4/4 | 4/4 |
| STOP/safety cases | 2/2 | 2/2 |
| Aggregate weighted score | **82.307%** | 72.213% |
| Peak server RSS | 32.724 GiB | 29.816 GiB |
| Total wall time | 346.63 s | 1,998.27 s |
| Crashes / incomplete jobs | 0 / 0 | 0 / 0 |

Qwen3.6 leads this measured score by 10.094 percentage points, with stronger hidden coding results and similar tool/safety outcomes. Qwen3.8 is dramatically slower in this setup, especially on the two long-context tasks. TTFT and actual device VRAM were unavailable; server RSS and wall-normalized output throughput are proxies. The full-stack owner-PC restart, PRIMARY→WORKER→fallback, and integrated Jeff/CMD/UX/Telegram/heartbeat/Browser/Computer Use acceptance gates were not completed. Therefore retain **PRIMARY = Qwen3.8-27B Q5**, set **LOCAL_WORKER = none**, and report `MODEL_STACK_OWNER_PROVEN=NO`. Do not deploy the tournament winner until the missing restart and owner-stack gates pass.

Hidden coding tests are included in this branch for reproducibility. Full raw outputs and score calculations are in `benchmarks/model-market-20261004/`; run instructions are in `REPRODUCE_MODEL_MARKET_20261004.md`. This tournament does not revalidate the previously reported Qwen3.8-pi result.

| Decision gate | Result |
|---|---|
| PRIMARY | Preserve Qwen3.8-27B Q5 |
| LOCAL_WORKER | None |
| `MODEL_STACK_OWNER_PROVEN` | **NO** |
| Full owner-stack restart/integration gate | Not run |

## Post-tournament Muse runtime diagnosis (2026-10-04)

The first Muse quick round is incomplete and remains unranked. A follow-on diagnostic isolated a runtime configuration fault: the installed Ollama model returns an empty `content` field after three completion tokens, and the isolated Bossman task fails with `EMPTY_RESULT`. Its Ollama `show` metadata lists stop strings for `<|begin_of_text|>`, `<|start|>`, and `<|message|>`. Meta's current Muse Glimmer llama.cpp guide specifies `--jinja`, a `muse-glimmer` API alias, `reasoning_strength: low` for bounded reasoning, and end tokens `<|end_of_text|>` plus `<|eot|>`; it warns against incorrect stop tokens. [Meta Muse Glimmer llama.cpp guide](https://dev.meta.ai/docs/muse-glimmer/llama-cpp)

Using the same locally stored Muse Q4 model and vision projector with `llama.cpp` b11223 Vulkan, `--jinja`, context 32768, reasoning budget 512, and low reasoning strength, the isolated Bossman CMD/API completed one exact-answer task (`MUSE_BOSSMAN_READY`). This proves that a corrected local Bossman route can answer a simple prompt; it does not complete Muse's 10-task round, prove tool reliability, or justify a LOCAL_WORKER promotion. The Ollama model and canonical Bossman configuration were left unchanged. Muse is image-input/text-output; Tencent Hy-Image is a separate image-generation provider and has not been integrated into Bossman.

## Market sources reviewed

- Official Qwen Qwen3.8-27B card: <https://huggingface.co/Qwen/Qwen3.8-27B>
- Official NVIDIA GLM 5.3 card: <https://huggingface.co/zai-org/GLM-5.3-Flash> and Strix community quant: <https://huggingface.co/PaoAI/GLM-5.3-Flash-PaoAI-ROCmFP4-STRIX-BALANCED-GGUF>
- Fable distill: <https://huggingface.co/TeichAI/Qwen3.8-27B-Fable-Distill>; Fable coding distill: <https://huggingface.co/khazarai/Qwen3.8-27B-Fable-5-Coding-Distilled>; GGUF: <https://huggingface.co/khazarai/Qwen3.8-27B-Fable-5-Coding-Distilled-GGUF>
- Terse-Coder: <https://huggingface.co/vwdubb/Swift-Qwen3.8-27b-Terse-Coder>; community ROCm/activity search: <https://huggingface.co/kingjones777/activity/community>
- Qwen3.6-35B-A3B official card: <https://huggingface.co/Qwen/Qwen3.6-35B-A3B>; Coder-Next: <https://huggingface.co/Qwen/Qwen3-Coder-Next>
- Ornith official card/evaluation: <https://huggingface.co/ornith-ai/Ornith-1.0-35B>; citation/eval repo: <https://huggingface.co/ornith-ai/Ornith-1.0-35B-FP8>
- Muse Glimmer: <https://huggingface.co/meta-models/Muse-Glimmer-30B>; GGUF community card: <https://huggingface.co/NANI-Nithin/Muse-Glimmer-30B-GGUF>
- Clef-Flash: <https://huggingface.co/Cloudflare/clef-flash>; repo tree and custom joint schema head: <https://huggingface.co/Cloudflare/clef-flash/tree/main>
- JEV-27B-VL: <https://huggingface.co/autotrust/JEV-27B-VL>
- Devstral Small 1.1: <https://docs.mistral.ai/models/devstral-small-1-1-25-07>; model card: <https://huggingface.co/mistralai/Devstral-Small-2507>
- Gemma4-12B official: <https://huggingface.co/google/gemma-4-12B>; function calling: <https://ai.google.dev/gemma/docs/capabilities/text/function-calling-gemma4>; named FABLE/Composer derivative: <https://huggingface.co/yuxinlu1/gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF>
- GPT-OSS-20B: <https://huggingface.co/openai/gpt-oss-20b>
- Nemotron Nano v2: <https://huggingface.co/nvidia/NVIDIA-Nemotron-Nano-12B-v2>; Nemotron 3.5 Lightning: <https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4>; reproducibility recipe: <https://github.com/NVIDIA-NeMo/Gym/tree/main/nemotron_recipes/lightning-3.5/reproducibility.md>
- Qwen quant evaluation protocol with runnable BFCL/LiveCodeBench details but explicitly no agentic claim: <https://github.com/nicosuter/Qwen3.8-27B-AWQ/blob/master/EVAL.md>
- Qwen3.8 BFCL/tau3 independent evaluation and discovered harness bugs: <https://github.com/Nicolas-Formenton/qwen3.8-27b-finetune-eval>
- Qwen3.8 performance-only test settings: <https://github.com/lengtsp/qwen3.8-27b-benchmark>

Leaderboard/search results were used only for candidate discovery. No external leaderboard result is counted as a local winner.

## Explicit no-download exclusions

MiMo-V2.6 Pro/Flash and Kimi K3 or any 150+ GB artifact were not downloaded. Colibri P3 was not downloaded. No model without a suitable AMD runtime or with an unverified/custom quant format was downloaded. Duplicate Qwen3.8 weights were not downloaded. Qwen3.8-pi received one diagnostic rerun using its local embedded template/default sampling; CODE-01 yielded no output after 414 seconds at 4096 tokens, and the run was stopped.


