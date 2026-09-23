# Bossman 1.1: three primary local models, 88 GB ceiling

Verified: 2026-09-22 UTC / owner run 2026-09-23 Prague. Machine: Ryzen AI Max+ 395, Radeon 8060S, 128 GB unified memory.

## Decision

Use three **primary role specialists**, one loaded at a time. This is a researched shortlist, not a claim of worldwide first place. Published model scores do not prove that a particular quantization, AMD driver and Bossman harness will achieve the same result. Weight artifacts, exact sizes, revisions and SHA256 values are verified; AMD execution and task quality remain owner-machine acceptance gates.

| Primary role | Official model | Selected artifact | Exact weights, decimal GB | Planned 32K working envelope, GB |
|---|---|---|---:|---:|
| Complex reasoning, architecture and difficult patches | `Qwen/Qwen3.8-27B` | Unsloth `Qwen3.8-27B-Q8_0.gguf` | 29.047 | 42.047 |
| Repeated code/test experiments and bounded tool execution | `Qwen/Qwen3-Coder-Next` | Official Qwen `Qwen3-Coder-Next-Q6_K/`, all four shards | 65.528 | 77.528 |
| Mandatory independent review, counterexamples and tool-contract criticism | `meta-models/Muse-Glimmer-30B` | Official `Muse-Glimmer-30B-KQuant-Dynamic-Q4_K_XL.gguf` | 19.654 | 31.654 |

These are assignments to evaluate, not a hidden fallback chain. Do not replace an unavailable reviewer with the builder and call that independent verification. The two Qwen models may share blind spots; Muse performs the independent review. Deterministic tests, evidence and promotion policy remain outside all model verdicts.

The operational manifest for the three candidates is [`tools/model_profiles.json`](../../tools/model_profiles.json), consumed by the shipped `model_fetch.py`. The original [`config/evolution/local-champions.json`](../../config/evolution/local-champions.json) remains the dated research snapshot; a test requires matching revisions, paths, sizes, SHA256 and memory plans. All three entries are optional, with `PENDING_OWNER_HARDWARE` activation. The downloader does not route a model to an agent or establish AMD support. Its 88 GB check is a planning gate; the owner still measures actual peak before admission. The catalog marks the model-card licence as unverified until read for commercial use.

For a local endpoint already running on the owner's machine, `model_bakeoff.py --url http://127.0.0.1:8088/v1 --tag candidate --out results --json --latency-probe --server-pid <llama-server PID>` records a separate streamed first-token delay and the provider's prefill rate when reported. It samples that server process's RSS when `psutil` is available. This is an additional small generation request, so enable it deliberately. Missing SSE, provider timings, `psutil` or PID stays `UNMEASURED`/null. Process RSS does not measure AMD GPU allocation; verify the real GPU/unified-memory peak separately before admitting the model under 88 GB.

## Why these three

- **Qwen3.8-27B:** the author's card reports SWE-bench Pro 61.7, Terminal Bench 2.1 73.0, and LiveCodeBench v6 90.3. This supports using it for difficult coding and agent planning. The published coding evaluation used a much larger context than our initial 32K profile; reproduce on our tasks before claiming equivalence. [Official card](https://huggingface.co/Qwen/Qwen3.8-27B).
- **Qwen3-Coder-Next:** an 80B-total / 3B-active coding specialist, trained for tool use, execution feedback and recovery, with a mature GGUF deployment path. Use it for the large volume of bounded patch/test attempts. Lower active compute suggests a useful throughput role; it does **not** establish measured speed on the 8060S. It is not claimed to beat Qwen3.8 in quality. [Official card](https://huggingface.co/Qwen/Qwen3-Coder-Next), [technical report](https://arxiv.org/abs/2603.00729).
- **Muse Glimmer-30B:** a separate Meta model family with agent and evaluator use cases. The author's card reports MCP Atlas 75.5, SWE-bench Pro 51.2 and SWE-bench Verified 76.0. Its official quantization has an explicit upstream llama.cpp path. Reviewer suitability is an engineering hypothesis: require it to find seeded defects and avoid fabricated findings before promotion. [Official card](https://huggingface.co/meta-models/Muse-Glimmer-30B).

Different benchmark harnesses, context sizes and task revisions must not be merged into one invented leaderboard. No reliable evidence establishes that these three are the globally best models, or that a week of use guarantees income or exponential improvement.

## Memory calculation and admission

All numbers here use **GB = 1,000,000,000 bytes**. The cap is deliberately 88,000,000,000 bytes, even if the UI labels GiB as GB. Unified RAM is shared: do not add 88 GB GPU memory on top of 128 GB system memory.

Planned envelope = verified weight bytes + KV/recurrent-state allowance + 6 GB runtime/buffer allowance + 4 GB additional headroom. Qwen3.8 gets 3 GB for state; Coder-Next and Muse get 2 GB each. At 32,768 tokens and F16 KV, the standard attention component is approximately 2.147 GB for Qwen3.8 and 0.805 GB for Coder-Next; their recurrent state is additional. Muse's conservative all-layers full-cache calculation is 1.745 GB; sliding-window reuse can reduce it. These are architecture-based estimates, not measured allocation peaks.

Start with **8,192 context, one slot**, then accept **32,768 context, one slot** after actual memory and output tests. Keep at least 32 GB for Windows/Linux, Bossman, browser and test containers. Stop the old server and confirm memory is released before loading the next model. Do not load all three together. Do not silently spill a supposedly GPU-resident champion into swapping and call it ready.

Selected downloads total **114.230 GB**. Reserve at least 130 GB of model storage plus separate space for the repository, environments and experiments. No weights were downloaded during this research.

## Exact artifacts

| Model | Quantization repository | Pinned revision | Files to download |
|---|---|---|---|
| Qwen3.8-27B | `unsloth/Qwen3.8-27B-GGUF` | `4ca720788d1e01f1bff70c033e0d0028fd02e502` | `Qwen3.8-27B-Q8_0.gguf` |
| Qwen3-Coder-Next | `Qwen/Qwen3-Coder-Next-GGUF` | `b82fb7382639d97b38fa7672e526c760c2fb358e` | `Qwen3-Coder-Next-Q6_K/Qwen3-Coder-Next-Q6_K-00001-of-00004.gguf` through `00004` |
| Muse Glimmer | `meta-models/Muse-Glimmer-30B-GGUF` | `70bf1b61ac09f91b24d39038091b41c582bc5d7a` | `Muse-Glimmer-30B-KQuant-Dynamic-Q4_K_XL.gguf` |

Full file sizes and SHA256 hashes are in the JSON manifest. Artifact metadata was checked with the Hugging Face model API, using `?blobs=true`; no repository Python code was executed.

## Owner-run instructions for Claude

1. Keep the 1.0 assembly intact. Integrate this manifest and role mapping in the separate 1.1 worktree first. Read the branch's main implementation/handoff document before changing runtime configuration.
2. Inspect the installed AMD driver, actual free UMA budget, OS and `llama-server --list-devices`. Prefer an official upstream **llama.cpp Vulkan** build on Windows/Linux. Do not use CUDA binaries on this GPU. Build instructions: [upstream Vulkan](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md#vulkan). A source build uses `cmake -B build -DGGML_VULKAN=ON`, followed by `cmake --build build --config Release`.
3. Record the exact runtime release/commit. Muse requires upstream **b10353 or newer**; this minimum is not itself proof of current AMD correctness. Use `--jinja`. For Muse, never use `<|eom|>` as a stop token; verify that final content and reasoning are parsed into their proper fields. [Official Muse deployment notes](https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF).
4. Download only the listed files at the pinned revisions. Verify SHA256 before serving. Downloading stays an explicit owner-run step; reading the manifest, running unit tests or starting Bossman must not trigger a 114 GB transfer.
5. Load and test one model at a time. Begin text-only, no MTP/DFlash and no vision projectors. Those additions need separate memory and correctness measurements.
6. Connect the existing Bossman OpenAI-compatible local adapter to `http://127.0.0.1:8088/v1`. Use the actual `model_id` as the server alias; check `/v1/models` after every model swap. Do not invent a native Claude provider compatibility layer.
7. Run the task admissions below, store results with exact model/quant/runtime/context/prompt hashes, then enable the verified role. Missing capability is `blocked`, not `passed`.

Explicit download commands (run on the owner's machine when preparing these selected models):

```bash
hf download unsloth/Qwen3.8-27B-GGUF Qwen3.8-27B-Q8_0.gguf --revision 4ca720788d1e01f1bff70c033e0d0028fd02e502 --local-dir models/qwen38
hf download Qwen/Qwen3-Coder-Next-GGUF --include "Qwen3-Coder-Next-Q6_K/*.gguf" --revision b82fb7382639d97b38fa7672e526c760c2fb358e --local-dir models/coder-next
hf download meta-models/Muse-Glimmer-30B-GGUF Muse-Glimmer-30B-KQuant-Dynamic-Q4_K_XL.gguf --revision 70bf1b61ac09f91b24d39038091b41c582bc5d7a --local-dir models/muse
```

Launch each line in turn, stopping the previous process first. Use `llama-server.exe` on Windows or the appropriate built binary path. These are commands for the initial 8K smoke; change `-c 8192` to `-c 32768` only after acceptance. The Coder-Next first shard loads its sibling shards automatically; all four must exist in the same directory.

```bash
llama-server -m models/qwen38/Qwen3.8-27B-Q8_0.gguf --alias Qwen/Qwen3.8-27B --host 127.0.0.1 --port 8088 --jinja -ngl 99 -c 8192 -np 1 -b 256 -ub 128 -ctk f16 -ctv f16 --temp 1.0 --top-p 0.95 --top-k 20
llama-server -m models/coder-next/Qwen3-Coder-Next-Q6_K/Qwen3-Coder-Next-Q6_K-00001-of-00004.gguf --alias Qwen/Qwen3-Coder-Next --host 127.0.0.1 --port 8088 --jinja -ngl 99 -c 8192 -np 1 -b 256 -ub 128 -ctk f16 -ctv f16 --temp 1.0 --top-p 0.95 --top-k 40
llama-server -m models/muse/Muse-Glimmer-30B-KQuant-Dynamic-Q4_K_XL.gguf --alias meta-models/Muse-Glimmer-30B --host 127.0.0.1 --port 8088 --jinja -ngl 99 -c 8192 -np 1 -b 256 -ub 128 -ctk f16 -ctv f16 --temp 1.0 --top-p 0.95 --top-k 64
```

Use the manufacturer's generation settings above initially. Qwen3-Coder-Next is non-thinking; do not require a reasoning block. For Muse use a system instruction `Reasoning strength: high` and a bounded output budget. Avoid a common shared prompt that silently disables model-specific tool parsing.

## Acceptance that makes the roles real

- **Builder:** fix at least three known failing repository scenarios in an isolated worktree; unchanged trusted tests must reproduce failure on baseline and pass on candidate. No benchmark/test edits count as a repair.
- **Worker:** exact tool name and JSON schema, two sequential calls, invalid-argument recovery, timeout and restart/resume. Persist external execution evidence, not just model text saying success.
- **Reviewer:** receive requirements, immutable diff and trusted evidence without the builder's self-score. Test on clean patches plus seeded faulty patches. Findings must include location, reproducible input and expected/observed behavior. Fail if it rubber-stamps seeded critical defects or fabricates blocking defects on clean cases.
- **Runtime:** non-empty final answer, no leaked tool markers, full intended context per slot, peak actual memory below 88 GB during prefill and generation, and completed representative tasks within the run budget. Record elapsed time and tokens/sec; do not copy NVIDIA/Apple performance claims onto AMD.
- **Promotion:** deterministic regression and held-out task gates decide acceptance. A reviewer verdict alone cannot publish, merge, spend, change approvals or rewrite the evaluation set. Existing repository policy still governs all actions.

**Integration still needed:** the current evolution CLI calls builder and reviewer inline and does not implement model unload/reload. The existing runtime manager must provide sequential role routing; model names alone do not do so. The current LocalProposer also explicitly sends temperature 0.1 and max_tokens 4096, overriding server sampling defaults. Wire per-model generation profiles and measure final JSON before enabling these roles. For the first live run, use the existing Claude adapter as builder and a single local Muse reviewer; OpenRouter/Gemini integration is described in `V1_1_FINAL_HANDOFF.md`.

After runtime integration, batch experiments by role to reduce model reload overhead: worker builds a bounded candidate set, reasoning builder handles selected hard cases, then unload and run the independent reviewer. Budget, cancellation and crash recovery remain active across all stages.

## Strong releases deliberately excluded from tomorrow's primary set

- **GLM-5.3-Flash:** 320B total parameters, not 18B total. The smallest listed Unsloth quant is already about **93.1 GB** before context/runtime, above the strict 88 GB ceiling. [Official card](https://huggingface.co/zai-org/GLM-5.3-Flash), [GGUF sizes](https://huggingface.co/unsloth/GLM-5.3-Flash-GGUF).
- **Qwen3.8-Flash-Next:** 125B backbone plus 51B n-gram tables and MTP. Listed Q4 is 111 GB; IQ3 is about 82 GB, leaving inadequate conservative operating headroom. Lower-bit variants exist, but no verified 8060S quality/latency result justified replacing the primary set. Host offload does not create additional unified RAM. [Official card](https://huggingface.co/Qwen/Qwen3.8-Flash-Next), [GGUF sizes](https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF).
- **Xing4.0-29B-A4B:** official 20.104 GB GGUF exists and fits by weight size. Its author's deployment tutorial uses a custom `xing4_0-port` branch and CUDA on RTX 3090. That does not establish upstream Vulkan readiness on 8060S. Keep outside the three primary models until its AMD implementation passes admission. [Official runtime tutorial](https://github.com/XingChen-AGI/Xing4.0-29B-A4B/blob/main/tutorial/llama.cpp/README_zh.md).
- **Nemotron 3 Super 120B:** a real, capable agent model, but larger quants consume much more of the available memory. Its published coding results did not establish a clear advantage for this specific role split; it was not selected simply to fill RAM. [Official card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-BF16), [quantization sizes](https://huggingface.co/unsloth/NVIDIA-Nemotron-3-Super-120B-A12B-GGUF).

## Artifact evidence

- [Qwen3.8 file manifest](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/4ca720788d1e01f1bff70c033e0d0028fd02e502)
- [Official Coder-Next Q6 shards](https://huggingface.co/Qwen/Qwen3-Coder-Next-GGUF/tree/b82fb7382639d97b38fa7672e526c760c2fb358e/Qwen3-Coder-Next-Q6_K)
- [Official Muse file manifest](https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF/tree/70bf1b61ac09f91b24d39038091b41c582bc5d7a)

Completion truth: research and artifact pinning complete; weights not downloaded; owner hardware tests not run; role-routing integration still requires the handoff above.
