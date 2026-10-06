# Viggle Qwen Image 2.1 Turbo Local Profile

Status: implementation/evaluation specification. This is not an installation receipt or an owner-machine PASS.
Date: 2026-10-02
Target: Windows 11, Ryzen AI Max+ 395 / Radeon 8060S (`gfx1151`) / 128 GB unified memory.

## Decision

Add Viggle Qwen-Image-2.1 Turbo v0.3 as a local image generation and instruction-editing profile. It is not a video model. Motion Studio must not route animation jobs to it. Keep the current Qwen Image path as the fallback until a same-machine comparison proves the Turbo profile better.

The upstream model is distributed under the Qwen Research License. Until a separate commercial licence is recorded, mark the profile `Research` and reject commercial requests before model loading. A runtime's open-source licence does not change the model-weight licence.

## Verified upstream behavior

The official model card currently identifies v0.3 (published 2026-09-29) as a six-step distilled LoRA. For six steps it requires the shipped scheduler, raw sigmas `[1.0, 0.9375, 0.875, 0.75, 0.5, 0.25]`, guidance 1.0, and LoRA scale 1.0. It supports text-to-image and instruction edits with one to three ordered reference images. The official r256 BF16 adapter is about 1.3 GB; the r128 adapter is about 0.7 GB.

The v0.3 nine-step hybrid runs seven Turbo steps, disables the LoRA, then completes two steps with the base model. The model card documents this for Diffusers and its demo Space only. Keep `VIGGLE_QUALITY` unavailable in WanGP until that runtime path is implemented and verified. For detailed text or difficult edits, offer the existing verified Qwen base profile instead.

## Windows runtime decision and current caveat

Primary evaluation runtime: isolated WanGP on its Windows AMD/TheRock path. Pin WanGP to commit `b8b18f8114e432eea8f3d7e853a51dd91fa99571` until a tested update is deliberately selected. Its Windows AMD guide lists `gfx1151` / Ryzen AI Max (Strix Halo), Windows 10/11, and Python 3.12. At that commit, WanGP's documented Viggle profiles stop at v0.2.1; v0.3 is newer than those presets. The Qwen Image 2.1 pipeline's six-step raw sigma list does match the official v0.3 list, so v0.3 can be added as an explicit local profile using the official v0.3 LoRA and that exact schedule. Do not select the upstream v0.2.1 preset and report it as v0.3.

WanGP exposes a Python API (`shared.api.init`, `session.submit_task`, `job.cancel`) rather than the Bossman HTTP contract. Run a small API shim inside the isolated WanGP environment, bound to `127.0.0.1`, and adapt Bossman's backend-neutral image interface to it. The shim exposes only image generation/editing routes and the supported v0.3 Fast profile. It must not expose WanGP's video surface to Bossman. The shim must not load a model at service startup or download weights in response to a job.

TheNoise remains `CANDIDATE_NOT_VERIFIED`: the current setup guide lists Linux x86_64 as its platform requirement and Strix Halo as the primary target. A Windows extraction note does not establish a supported Windows runtime or an owner-machine PASS.

## Bossman adapter seam

Preserve the visible pipeline:

`Intent -> Policy -> Media job -> Local runtime -> Artifact verification -> Result`

Keep Motion Studio and Jeff/CMD independent of WanGP. Use a backend-neutral adapter with `health`, `generate`, `edit`, and `cancel`. Requests carry a job id, prompt, allowlisted dimensions, seed or explicit random selection, zero to three validated local references, privacy class, commercial-use flag, deadline, and cancellation token. Results carry the backend/model/LoRA identities and hashes; resolved schedule, guidance, seed and size; cold/warm state and timings; memory readings when available; artifact path, MIME, size, dimensions and SHA-256; verification status, warnings, and fallback history.

Expose only named Bossman profiles. Do not let the simple UX pick arbitrary step, sigma, CFG, or LoRA values:

| Profile | Runtime | Status |
|---|---|---|
| `VIGGLE_FAST` | v0.3, six steps, exact sigma list, guidance 1.0, LoRA 1.0 | Implement after the v0.3 local profile is pinned and measured |
| `VIGGLE_EDIT` | Same schedule, one to three ordered references | Implement after edit verification |
| `VIGGLE_QUALITY` | v0.3 nine-step hybrid | Disabled in WanGP pending explicit runtime support |
| `QWEN_BASE_FALLBACK` | Existing verified Qwen Image path | Keep as separate profile for complex edits |

The API shim and adapter must fail closed if the configured model revision or LoRA hash differs from the pinned manifest. Do not fall back silently between local/cloud runtimes or between model profiles.

## Privacy, resource, and artifact rules

- Bind the shim to `127.0.0.1`; reject remote URLs and paths outside Bossman's media workspace.
- Never send prompts or references to a cloud service without explicit owner approval. `LOCAL_ONLY` and `PRIVATE` never use cloud fallback.
- Normalize and re-encode untrusted references before inference; accept only bounded supported image types and sizes.
- Queue when memory is busy; never kill unrelated Bossman workers.
- STOP requests cancellation in both the Bossman queue and WanGP. If outcome is ambiguous, report `CANCEL_UNKNOWN` and quarantine any late artifact.
- Write into a job-scoped temporary directory. Verify non-empty file, successful image decode, dimensions, content MIME, SHA-256, metadata manifest, no reference overwrite, and readability after worker release before promoting it to the media library.
- Preserve edit input, output, and a manifest linking them. Never overwrite the original.
- Jeff can request a media job but cannot approve commercial use, access owner-only images, or widen policy.

## Licence gate

Use `research_eval` for this model. Reject `commercial_use=true` before starting or loading the model. Do not use it for paid FreshVibes, SwapMe, advertising-client, or resale deliverables until a separate commercial licence is recorded. Keep commercial alternatives separate with their own verified licences. Permit private evaluation and internal benchmark jobs only.

## Owner-machine evidence required

Record the exact Bossman SHA; WanGP commit; Python, AMD driver, PyTorch/ROCm, Triton and attention versions; Qwen base revision; official LoRA filename, revision and SHA-256. Download only the selected base components and adapter; do not clone the full historical/quantized Hugging Face repository. Before labeling the runtime ready, prove `torch.cuda.is_available()` and the active `gfx1151` device in the isolated environment.

Run one cold request and three warm requests per benchmark case, unloading each model before switching: Russian and English photorealistic 1024² prompts; short readable BOSSMAN-42 product text; a short multi-element poster; one-reference background replacement; identity-preserving clothing edit; two-reference and three-reference compositions; STOP during load and denoising; backend restart and retry; Bossman restart and job/artifact recovery. Record median/p95 warm latency, cold load, peak memory, artifact verification, adherence, text accuracy, identity drift, repeated figures, STOP latency, recovery, and external network connections during `LOCAL_ONLY`.

Run the comparison through Bossman UX or CMD against the current Qwen path, v0.3 Fast, v0.3 Quality only if implemented, and base Qwen fallback. Upstream latency claims are targets only, never owner evidence. Do not mark `OWNER_VERIFIED` until every acceptance test passes on the AI Max computer and its manifest, benchmark and network audit are retained against one exact Bossman SHA.

Suggested evidence layout:

```text
artifacts/viggle_turbo/<run-id>/manifest.json
artifacts/viggle_turbo/<run-id>/benchmark.json
artifacts/viggle_turbo/<run-id>/network_audit.json
artifacts/viggle_turbo/<run-id>/outputs/
docs/evidence/VIGGLE_TURBO_OWNER_RUN_<date>.md
```

## Evidence status

### Owner runtime probe — 2026-10-02

Created an isolated Python 3.12.10 environment at
`%LOCALAPPDATA%\Temp\Bossman-WanGP-Viggle-v03` and installed the pinned Windows
TheRock wheels from WanGP's documented `gfx1151` instructions (`torch 2.13.0+rocm10.0.0`).
`import torch` is blocked before GPU enumeration: Windows Code Integrity event
3077 says Smart App Control rejected unsigned `hiprtc0715.dll` under policy
`0283ac0f-fff1-49ae-ada1-8a933130cad6`. The blocked DLL SHA-256 is
`19b67aa86ba371c49b2def5e5c863346e81c1b6d67caab125cf10a8c405e0043`; its
Authenticode status is `NotSigned`. Smart App Control is active, and Microsoft
documents that it blocks unknown unsigned code and has no per-file bypass.
No Qwen/Viggle weights were downloaded and no image generation was attempted.
The probe manifest is `%LOCALAPPDATA%\Temp\Bossman-WanGP-Viggle-v03\runtime-probe.json`.

This is an owner-machine runtime failure, not a model-quality result. Do not
disable Smart App Control or relabel TheRock GPU support as PASS. WSL is not
installed on this machine, so TheNoise's Linux runtime is not an available
owner path in this probe.

| Area | Status |
|---|---|
| v0.3 six-step settings, edit capability, model files and research licence | Verified from official upstream card |
| WanGP Windows AMD guide lists `gfx1151` | Documented upstream; owner PyTorch import blocked by Smart App Control |
| WanGP exact commit includes v0.2.1 presets and matching six-step sigma values | Source inspected; v0.3 profile still needs explicit local configuration |
| WanGP API is an in-process Python API | Documented upstream; Bossman loopback shim not implemented |
| TheNoise Strix Halo results | Upstream Linux benchmark/setup only |
| Bossman backend adapter and licence gate for Viggle | Not implemented |
| Owner Windows generation/edit, timings, cancellation, privacy egress and recovery | Blocked before GPU enumeration; no weights downloaded |
| Commercial licence | Not recorded; commercial use blocked |

## Upstream sources

- [Official Viggle model card, v0.3 files, settings and licence](https://huggingface.co/Viggle/Qwen-Image-2.1-viggle-turbo)
- [Official WanGP Windows AMD/TheRock installation guide](https://github.com/deepbeepmeep/Wan2GP/blob/b8b18f8114e432eea8f3d7e853a51dd91fa99571/docs/AMD-INSTALLATION.md)
- [WanGP Python API](https://github.com/deepbeepmeep/Wan2GP/blob/b8b18f8114e432eea8f3d7e853a51dd91/docs/API.md)
- [WanGP Qwen Image 2.1 scheduler implementation](https://github.com/deepbeepmeep/Wan2GP/blob/b8b18f8114e432eea8f3d7e853a51dd91/models/qwen21/pipeline.py)
- [TheNoise setup/platform scope](https://github.com/lemonade-sdk/thenoise/blob/main/docs/setup.md)
- [Microsoft Smart App Control behavior and unsigned-code blocking](https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/overview)
- [Microsoft Smart App Control FAQ: no per-file bypass](https://support.microsoft.com/en-us/windows/security/threat-malware-protection/smart-app-control-frequently-asked-questions)
- [TheRock WinError 4551 issue](https://github.com/ROCm/TheRock/issues/2607)
