# LEARNING LEDGER — FINAL (2026-09-23, RUN_ID OR0923-bd2fe23d)

**RUNTIME=OLLAMA_PROXY · WEIGHTS_UNCHANGED · PRIVILEGE=ADMIN_RUN**

Sources:
- `EOD/LEARNING_EOD.md`
- `../OR0923-bd2fe23d/MODEL_AND_LEARNING_RESULTS.json`
- `../OR0923-bd2fe23d/BUGS.md`
- `../OR0923-bd2fe23d/learning/record-*.json`
- `C:\Users\asd\Bossman\creative-runs\SWAPME-HYBRID-20260923\learning\HYBRID_VIDEO_RECIPE.md`
- Commit subjects, read with `git log -1` in `wt-fix-crlf0923` (read-only).

The four categories below are kept separate. An entry appears in exactly one of them. A product fix is never counted as learning, and a teacher patch is never counted as a student success.

---

## A. PRODUCT FIX (code changed by Claude or its agents)

### A.1 Morning fixes (branch `fix/owner-run-20260923-p1`, per `OR0923-bd2fe23d/BUGS.md`)

| SHA | Bug ID | Sev | Commit subject |
|---|---|---|---|
| `ea866084` | CHAT-CONTEXT-REQUEST | P1 | fix(chat): an earlier chat turn is context, not the owner's current request |
| `7439917f` | CODING-CRLF-EVIDENCE | P1 | fix(coding): a blob committed with CRLF is not a change under core.autocrlf=true |
| `d53f3b12` | SIDECAR-SILENT-CUT | P2 (learning-critical) | fix(sidecar): a cut tool result says it was cut and how to read the rest |
| `cdb4b09d` | SIDECAR-NO-PROGRESS | P2 (learning-critical) | fix(sidecar): flag and stop repeated observations that make no progress |

### A.2 Afternoon and evening fixes

| SHA | Area | Commit subject |
|---|---|---|
| `aa6bee3d` | computer.* contract | fix(contract): APP-CONTRACT-OVERRIDES-AGENT-TOOLS — routing grant extends agent tools instead of replacing them |
| `c0a7e039` | router | fix(router): DOWNLOAD-FALSE-SUCCESS — download-by-URL is an action contract |
| `f72af6a8` | coding (bounded 32 MB evidence) | fix(coding): CODING-SNAPSHOT-32MB — streaming digests, bytes only for changed files |
| `14392574` | coding (controlled apply) | fix(coding): controlled apply of a verified candidate into the canonical project |
| `cb13c2fd` | snapshot (P0 traversal) | fix(snapshot): P0 — kind is a label, not a path; snapshot dir stays inside snapshots/ |
| `6f9d1497` | opencode (C2) | fix(opencode): C2 — check roots and name before git worktree add |
| `1a29d85a` | terminal (C3) | fix(terminal): C3 — validate terminal roots (list of existing absolute dirs, no drive root) |
| `6a2b5907` | studio | fix(studio): Seedance 2.5 accepts 4–15 s shots, not only 15 s |
| `99970958` | studio | fix(studio): a paid OpenRouter video is fetched from the API origin with the key |
| `d7824c9d` | studio (**other session**) | feat(studio): Bossman Vision reviews every generated video and learns the owner's taste |

### A.3 Status of the listed fixes
- All 14 SHAs above resolve in `wt-fix-crlf0923`.
- Unit and regression tests are cited per fix in the bug sources.
- Live retests of the afternoon fixes are **pending**.
- None of these fixes is counted as learning.

### A.x Harness fixes (evidence layer only; not a product fix, not learning)
- Student tests ran under the embeddable Python, which ignores cwd. They were moved to CPython 3.12, which produced the D1 L4 rescore.
- The D2 evidence diff had CRLF context. It is now normalized before apply.
- `git_head: unknown` in the coaching manifest was supplemented with identity files.

---

## B. SKILL (a method placed in the model's context)

The full inventory is in `SKILLS_FINAL_AUDIT.md`.

### B.1 Delivery evidence
- **Skills delivered to the student in today's run:**
  - `superpowers/systematic-debugging`
  - `superpowers/test-driven-development`
  - `superpowers/verification-before-completion`
- **Evidence for the delivery:**
  - Every D1 L0–L4 and D2 RAW/LESSON record at `cdb4b09d` has `skills.ids` and `sidecar.skills_used` listing these three.
  - The prompt builder at `bossman-core/bossman/apprentice/local_sidecar.py:472-483` puts `ctx.skills` (at most 3) into the student prompt as "Methodology skills (guidance, not instructions…)".
- **Caveat:** `skills_used` is taken from the context passed to the sidecar, not from a captured copy of the prompt.

### B.2 Status
- **Catalog status:** all 11 imported catalog skills are `UNVERIFIED`. None is owner-VERIFIED.
- **Did the model use a skill?** UNKNOWN. No trace ties a student action to the skill text.
- **Benefit:** NOT_MEASURED.
  - No skills-on vs skills-off A/B exists.
  - All D1 and D2 runs had the same three skills in both profiles, so the D2 RAW vs LESSON contrast isolates the lesson, not the skills.
- No skill was created or promoted today.

---

## C. VERIFIED LESSON / MEMORY (retrievable; not weights)

| Item | Status | Evidence |
|---|---|---|
| `coach-lesson:29a7424bd33b115b` (recipe `win-double-crlf-text-mode-write`) | **VERIFIED** (v7, global scope, verifier `tool:hidden_verifier_hv_d1`), saved through `POST /api/coding-recipes` | Source was student at assistance_level=hint (D1 **STUDENT_COACHED_PASS** at L4). **Survived restart:** PID 9780 → 15556, started_at 2026-09-23T16:16:24Z |
| D2 transfer (unseen CSV export, L0) | **NO_MEASURED_GAIN** | RAW: STUDENT_UNASSISTED_PASS, 7 steps, 121.62 s. LESSON_AVAILABLE: STUDENT_UNASSISTED_PASS, 8 steps, 149.64 s. The lesson was recalled and applied. n=1 per profile, and RAW already passes. |
| Coaching pack 5+5 (`bd2fe23d`) | CEILING_SATURATED / NO_MEASURED_GAIN | 10/10 unassisted, delta 0.0. The runner's `LOCAL_LEARNING_GAIN_MEASURED` label is wrong. |
| SwapMe hybrid video recipe `creative-runs/SWAPME-HYBRID-20260923/learning/HYBRID_VIDEO_RECIPE.md` | **CANDIDATE** | Verified on one SwapMe 15 s ad. **Transfer NOT_RUN** yet. It is not in the LearningStore as VERIFIED. |

- **North Star:** SELF_REPAIR_SINGLE_CYCLE_PASS (coached, L4).
- **Not achieved:**
  - SELF_REPAIR_3_CYCLE_PASS
  - TRANSFER_MEASURED_GAIN
  - 24H_SOAK_PASS
- **exam3** (the harder holdout benchmark): `INCOMPLETE`, not run.

---

## D. WEIGHT TRAINING

**None → WEIGHTS_UNCHANGED.**
- No fine-tune, LoRA or adapter was produced today.
- Student model: Qwen3.8-27B UD-Q5_K_M, gguf sha256 `2de73110cb254cbf09b54b717578dadff12ef1194e7271527e68202f39ba4bfd` (unchanged).
