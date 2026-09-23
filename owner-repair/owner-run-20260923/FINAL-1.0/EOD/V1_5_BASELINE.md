# V1.5 BASELINE — recorded 2026-09-23

> **1.0 is NOT frozen.**
> - There is no exact-SHA CI run.
> - There is no Windows-100 pass.
>
> **Under the master prompt, work on 1.5 features must not start yet.** This file records where things stand. It does not authorize 1.5 work.

## Identity

| Field | Value |
|---|---|
| BASE_SHA | `d7824c9d`: the convergence head, **NOT certified**. Subject: "feat(studio): Bossman Vision reviews every generated video and learns the owner's taste", from another session. |
| RELEASE_SHA | `e0bf948d`: `release/bossman-owner`, **untouched**. Subject: "docs(terminal): include Bossfield continuity fix in Claude handoff". |
| ZIP_SHA256 | **pending**: the ZIP is being built. |
| RUNTIME | OLLAMA_PROXY. The student is Qwen3.8-27B UD-Q5_K_M, gguf sha256 `2de73110…4bfd`, on Ollama 0.34.3. |
| WEIGHTS | WEIGHTS_UNCHANGED |

## Defect state

| Level | Count | Note |
|---|---|---|
| P0 | **0 known in code** | The snapshot traversal P0 is fixed in `cb13c2fd`. |
| P1 | **0 known in code** | See the fix list below. **Live retests are pending.** |

The P1 fixes are:
- `ea866084` CHAT-CONTEXT-REQUEST
- `7439917f` CODING-CRLF-EVIDENCE
- `f72af6a8` CODING-SNAPSHOT-32MB
- `c0a7e039` DOWNLOAD-FALSE-SUCCESS
- `aa6bee3d` APP-CONTRACT-OVERRIDES-AGENT-TOOLS
- `14392574` controlled apply
- `6f9d1497` C2
- `1a29d85a` C3

The P2 items in `OR0923-bd2fe23d/BUGS.md` stay OPEN unless listed as fixed there.

## Areas

| Area | Status | Basis |
|---|---|---|
| TERMINAL | Fixes in code; not certified | C3 `1a29d85a` validates terminal roots. The P2 ConHost paste, Terminate-batch prompt and prompt look-alike issues are still OPEN (BUGS.md). |
| MEMORY | Lesson persistence **PASS** | `coach-lesson:29a7424bd33b115b` (VERIFIED) survived a restart (PID 9780 → 15556) and was recalled and applied in D2 LESSON. The P2 issue MEMORY-NOT-CONFIGURED is still OPEN. |
| JEV (evidence / verifier layer) | **CONTRACT_VERIFIED + SHADOW_PASS** | Contract tests pass. Shadow run only; not enforced as the release gate. |
| SELF_REPAIR | **SINGLE_CYCLE_PASS (coached, L4)** | For D1, the student wrote the patch and the regression test; hv_d1 passes on the candidate and fails on the base; the negative control was rejected. SELF_REPAIR_3_CYCLE_PASS was **not achieved**. |
| TRANSFER | **NO_MEASURED_GAIN** | D2: RAW and LESSON both passed unassisted, with n=1. The harder holdout benchmark exam3 is `INCOMPLETE` and has not been run. |
| SKILLS | Delivered, benefit NOT_MEASURED | 3 catalog skills reach the student prompt. No A/B has been run. See `SKILLS_FINAL_AUDIT.md`. |

## KNOWN_LIMITATIONS
- **Release process:**
  - 1.0 is not frozen: no exact-SHA CI, no Windows-100 pass.
  - BASE_SHA `d7824c9d` is not certified.
  - The ZIP sha256 is not yet known.
- **Live retests pending** for the afternoon fixes: computer.* contract, download contract, 32 MB evidence, controlled apply, P0 snapshot, C2, C3, and the Seedance and OpenRouter studio fixes.
- **Learning gaps:**
  - No measured learning gain: the coaching pack is saturated and the D2 transfer showed NO_MEASURED_GAIN.
  - 24H_SOAK_PASS has not been achieved.
  - The SwapMe hybrid video recipe is a CANDIDATE only; its transfer has NOT_RUN.
- **Open P2s** in BUGS.md: DOWNLOAD-VERIFY-LOOP, AGENT-TOOLS-NO-UI, EFFECT-RETRY-FALSE-FAIL, WINDOW-CLOSE-LINGERING-BACKEND, TWO-PHASE-SUBMIT-DRAFT, RESUME-AGENT-LOST, CONHOST-PASTE, and others.
- **Environment:**
  - Smart App Control blocks unsigned llama.cpp, so the runtime goes through the Ollama proxy.
  - The session ran elevated (ADMIN_RUN).

## Gate to start 1.5
1.5 feature work may begin only after both of these hold:
- 1.0 is frozen at an exact SHA, with green CI on that SHA and a Windows-100 pass.
- The ZIP sha256 is recorded.
