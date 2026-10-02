# Qwen3.8-27B owner-hardware benchmark

**Date:** 2026-10-02  
**Target:** Ryzen AI Max+ 395 / Radeon 8060S gfx1151 / 128 GB unified memory / Windows 11  
**Status:** NOT RUN — no model installation or benchmark result is claimed  
**Bossman route:** unchanged; Qwen3.8-27B remains a candidate, not the selected default

## Candidate

Evaluate the exact local Qwen3.8-27B artifact separately from the current Bossman MAIN and FAST models. Record repository/model ID, revision, quantization, file SHA-256, runner/runtime version, context size, GPU/CPU offload and generation settings. Do not infer owner performance from model-card or community benchmark figures.

This is a local multimodal language model candidate for text and image/video understanding; it is not an image-generation model.

## Existing Bossman benchmark scope

Use the established HW-01 and local apprentice matrix in:

- [Owner-Hardware Model Stack](../../tests/owner_hardware/MODEL_STACK_2026-09-20.md)
- [Local Qwen Apprentice Benchmark](../evo/LOCAL_QWEN_APPRENTICE_BENCHMARK.md)

At minimum, the Qwen3.8-27B lane covers:

1. Conversation and multi-turn context.
2. Coding task.
3. Repository edit plus tests through the normal Bossman coding/tool path.
4. Long-context retrieval.
5. Structured output and tool call.
6. Bossman restart persistence.
7. Image understanding using a local, non-sensitive fixture, if the selected runtime supports image input.

For apprentice evaluation, preserve the existing categories: `STUDENT_UNASSISTED_PASS`, `STUDENT_COACHED_PASS`, `TEACHER_PATCH`, and `FAIL`. Do not merge these into one success rate. Include unseen holdouts and report sample size.

## Safe run procedure

1. Record free disk, free unified memory, active Ollama models and current job state.
2. Verify the model ID/revision and license; capture the exact downloaded file hash.
3. Install/import under a separate Ollama model name. Do not change Bossman routing or remove existing models.
4. Confirm the API health response and run one prompt smoke test.
5. Run benchmark cases sequentially against the current MAIN/FAST baseline under fixed prompt, context, tool access, attempt budget and timeout.
6. Record per-case correctness, tool/schema validity, tests, latency, prompt/eval token counts, peak memory, failures and owner interventions.
7. Unload only the candidate after the run; verify existing Bossman model routes remain available.
8. Save a sanitized evidence manifest bound to exact model and Bossman revisions.

Do not evict or stop a currently serving Bossman model to make room without checking active work and a safe route handoff first. Do not call missing telemetry zero.

## Owner-run status

On 2026-10-02, the local command and Computer Use executors failed to start, so disk/memory/model presence could not be rechecked. No Qwen3.8 files were pulled, no local inference was executed and no benchmark samples were collected. Status: **NOT INSTALLED / NOT RUN / NOT PASS**. The exact benchmark should resume when local execution is available.

## Evidence directory

Use a run-scoped directory, for example:

`artifacts/qwen38_owner/<run-id>/manifest.json`  
`artifacts/qwen38_owner/<run-id>/benchmark.json`  
`artifacts/qwen38_owner/<run-id>/outputs/`

Keep private images and raw sensitive prompts local. Store only sanitized results and hashes in the public repository.
