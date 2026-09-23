# LEARNING EOD — 2026-09-23 (RUN_ID OR0923-bd2fe23d)

**RUNTIME=OLLAMA_PROXY · WEIGHTS_UNCHANGED** · PRIVILEGE=ADMIN_RUN
Student: Qwen3.8-27B UD-Q5_K_M (gguf sha256 `2de73110…4bfd`), Ollama 0.34.3, ctx 65536, reasoning off (proxy `reasoning_effort=none`), temperature 0, max_steps 40, executor bossman-local-sidecar via `bossman code` (CLI).
Sources: `OR0923-bd2fe23d/MODEL_AND_LEARNING_RESULTS.json`, `learning/{COACHING_EPISODES.jsonl, BENCHMARK_MANIFEST.json, LESSON_SAVED.json, record-*.json, identity-*.json}`, `REPORT_RU.md`, `CHECKPOINT.md`, `exam-sealed-0923/exam3/MANIFEST.json` (status fields only; hv_* files not opened).

## 1. Results (exact values)

### Coaching pack 5+5 (SHA `bd2fe23d`)
- Result: **10/10 unassisted; coached = unassisted = 1.0** (holdout pass@1 unassisted 1.0, coached 1.0, delta **0.0**).
- Labels: **CEILING_SATURATED**, **NO_MEASURED_GAIN**, WEIGHTS_UNCHANGED.
- Lessons in store: 0 at start, 0 after the run.
- Evidence defect: `learning/coaching-pilot-main/summary.md` has the runner's automatic status `LOCAL_LEARNING_GAIN_MEASURED`. That label is wrong: the delta was 0.0. The authoritative label is the one above. Fix the runner's labelling before the next pack.

### D1 — real defect: CR CR LF in every `.cmd` launcher of the Windows archive (TRAIN)
- Pre-fix (SHA1 `bd2fe23d`), attempts L0–L3: FAIL (max_steps). Labels **PRE_FIX_CONFOUNDED / NOT_MODEL_FAILURE**. Cause: tool output was silently cut at 6000 characters and nothing stopped repeated calls (an orchestration problem). These attempts do not count as model failures.
- SHA2 `d53f3b12`, L0: FAIL (max_steps). The loop continued because there was no detector yet.
- SHA3 `cdb4b09d` ladder:

| Level | Result | stop_reason | steps | duration_s | changed files |
|---|---|---|---|---|---|
| L0 | FAIL | no_progress_loop | 22 | 215.83 | none |
| L1 | FAIL | no_progress_loop | 34 | 329.01 | none |
| L2 | FAIL | no_progress_loop | 22 | 263.45 | none |
| L3 | FAIL | max_steps | 40 | 295.99 | none |
| L4 | PASS after rescore | finished | 25 | 365.93 | `tools/build_windows_bundle.py`, `tests/test_windows_bundle_launcher_line_endings.py` |

- **Who fixed it:** the local student (Qwen). It wrote both the patch and the regression test. coding_task_id `06d240faac9e`. No teacher patch was applied. The L5 patch was prepared but not needed.
- **Hint level:** **L4 (approach)** → **STUDENT_COACHED_PASS**. It is not unassisted.
- **Verifier:** sealed hidden verifier `hv_d1` (sha256 `d8edecbd…7773`), run by `tool:hidden_verifier_hv_d1` (external_tool, run id `hv_d1-rescore-OR0923-L4`).
  - hv_d1: **PASS on the candidate, FAIL on the base**.
  - Student regression test: fails on the base, passes on the candidate.
  - Diff size: 3995 bytes.
- **The first L4 FAIL was a HARNESS bug:** the student's tests ran under the embeddable Python, which ignores cwd. After the rescore under CPython 3.12, the attempt passes.
- **Negative control:** a deliberately wrong LF-only patch (`write_bytes(body.encode())`) was **REJECTED** by hv_d1 (FAIL) and by the student regression test (FAIL). The lab repo was left unchanged.
- **Failure class, L0–L3:**
  - MODEL: it finds the cause line by search but never moves on to an edit, and it ignores the repeat warnings.
  - TOOL: literal `\r` is ambiguous inside a regex search.

### Lesson and restart
- Saved via `POST /api/coding-recipes` into the canonical LearningStore.
  - Lesson id: **`coach-lesson:29a7424bd33b115b`**
  - Recipe: `win-double-crlf-text-mode-write`
  - Status: VERIFIED, version 7, scope global
  - Verifier: `tool:hidden_verifier_hv_d1`
- Content: written from the D1 wording plus the generic cause class only, with no knowledge of D2. Provenance: source=student, assistance_level=hint.
- **Restart: PASS.** The lesson survived a full restart: PID 9780 → 15556, started_at 2026-09-23T16:16:24Z.

### D2 — TRANSFER-UNSEEN (CSV export, `open(...,'w')` + `'\r\n'.join`)
- **Unseen: yes.** It was sealed outside the student roots and revealed at 2026-09-23T16:21:09Z, after the restart. No hints were given (L0 only). hv_d2 sha256 `7855d5af…4452`.

| Profile | Result | steps | tool calls | duration_s | task id | memory / recipe |
|---|---|---|---|---|---|---|
| RAW (no memory) | STUDENT_UNASSISTED_PASS | **7** | 9 | **121.62** | `f9562fc2969e` | none |
| LESSON_AVAILABLE | STUDENT_UNASSISTED_PASS | **8** | 10 | **149.64** | `22e5e263a24f` | recalled `coach-lesson:29a7424bd33b115b`, applied `win-double-crlf-text-mode-write` |

- In both profiles, hv_d2 PASSED on the candidate and FAILED on the base. The student's regression test fails on the base and passes on the candidate.
- The lesson was recalled and applied, but it did not help. RAW already solves D2 without it, and the lesson run was 1 step and about 28 s slower. There is n=1 per profile, so this is a pilot result only.

### Summary labels
- **TRANSFER = NO_MEASURED_GAIN**. With n=1 and RAW already at PASS, D2 cannot show a gain. A real transfer claim needs a harder holdout; see the benchmark section below.
- **North Star = SELF_REPAIR_SINGLE_CYCLE_PASS (coached, L4)**.
  - Not achieved: SELF_REPAIR_3_CYCLE_PASS, TRANSFER_MEASURED_GAIN, 24H_SOAK_PASS.

## 2. Who contributed what

Rule: a teacher patch never counts as a student success. Product and harness fixes are Claude's work and are never counted as learning.

| Bucket | Contribution | Counted as student success? |
|---|---|---|
| **LOCAL_STUDENT** (Qwen3.8-27B via OLLAMA_PROXY) | Coaching 5+5: 10/10 with no help (the pack is saturated). | Yes, but it proves nothing about learning (ceiling). |
| | D1 at L4: its own patch in `tools/build_windows_bundle.py` and its own regression test, confirmed by hv_d1. | Yes, as **STUDENT_COACHED_PASS**, not unassisted. |
| | D2 RAW and LESSON: its own patch and test at L0, confirmed by hv_d2. | Yes, **STUDENT_UNASSISTED_PASS**. |
| **CLAUDE_TEACHER** | Hints L1–L4 for D1, where L4 gives the exact write line and the fix approach. | No. |
| | Choosing D1 and D2, the hint ladder, sealing hv_d1 and hv_d2, the negative-control patch. | No. |
| | Composing the recipe fields from the D1 wording and cause class. | No. |
| | Teacher patch (L5): prepared, **not used**. | No. |
| **PRODUCT_FIX** (Claude, fix branch `fix/owner-run-20260923-p1`) | SHA2 `d53f3b12`: (1) a stale «Запомни…» turn no longer breaks every later chat turn; (2) CRLF handling in the coding path; (3) the sidecar now marks cut tool output instead of truncating it silently at 6000 characters. | No. |
| | SHA3 `cdb4b09d`: NO_PROGRESS / repeat detector (`no_progress_loop` stop). | No. |
| | Open product P1s: the coding path refuses the full Bossman repo (80 MB > `_MAX_SNAPSHOT_BYTES` 32 MB), and evidence mismatch under `core.autocrlf=true`. | n/a |
| **HARNESS_FIX** (Claude, evidence layer only) | Student tests had run under the embeddable Python, which ignores cwd. Moved to CPython 3.12, giving the L4 rescore. | No. |
| | CRLF context in the D2 evidence diff is now normalized before apply. This also recorded product P2. | No. |
| | Found earlier in the run: the GUI measurement picked up a stale card, and a heredoc corrupted `\r` and `\`. | No. |
| | `git_head: unknown` in the coaching manifest was supplemented with `RUNTIME_IDENTITY_SHA2.json` and `identity-*.json`. | No. |

## 3. Tomorrow's benchmark — exam3 (not run today)

**Status per `exam-sealed-0923/exam3/MANIFEST.json`: `INCOMPLETE`.** Work stopped on the coordinator's request (owner shutdown). No case is committed or self-checked, so **exam3 is not usable yet**. No student run may start until every step below passes for all 6 cases.

| Pair | Train (role=train) | Status | Holdout (role=holdout, harder) | Status |
|---|---|---|---|---|
| 1 text/binary I/O, newline translation | **T1 `e3t1`** | INCOMPLETE: sources and tests written but not committed; `hv_e3t1.py` written, not run | **H1 `e3h1`** | INCOMPLETE: only `__init__.py`, `store.py`, `report.py` exist. Missing: `journal.py` (the defect file), tests, hv, ref/neg, task, allow, git, self-check |
| 2 timezone | **T2 `e3t2`** (naive vs aware day totals) | INCOMPLETE: sources and tests written but not committed; `hv_e3t2.py` written, not run | **H2 `e3h2`** (DST day-window totals) | INCOMPLETE: empty skeleton, everything missing |
| 3 Windows paths | **T3 `e3t3`** (`os.path.join` with an absolute part) | INCOMPLETE: sources and tests written but not committed; `hv_e3t3.py` written, not run | **H3 `e3h3`** (case- and slash-insensitive root containment) | INCOMPLETE: empty skeleton, everything missing |

Repos live at `C:\Users\asd\Bossman Test 0923\projects\exam3\<id>`. Hidden verifiers live only in `C:\Users\asd\Bossman\exam-sealed-0923\exam3\`, outside the student roots.

### Steps to finish, in order, done by the teacher and not the student

1. **Finish the holdout sources.** The holdouts must be different code paths from their train cases and harder than them. None may reuse train wording.
   - H1: write `journal.py` (the defect file) and the tests.
   - H2: write the full repo — the defect in the DST day window, plus tests.
   - H3: write the full repo — the root-containment defect, plus tests.
   - The existing tests must pass on the defective base, so the defect is not visible to them.
2. **Write the hidden verifiers for the holdouts:** `hv_e3h1.py`, `hv_e3h2.py`, `hv_e3h3.py`, stored only in the sealed dir.
3. **Commit each repo.** `git init` in each of the 6 repos with `core.autocrlf=false` (lesson from the D1 evidence mismatch). Then commit the base once and record the base SHA.
4. **Create a ref diff and a neg diff per case**, stored in the sealed dir:
   - `ref_<id>.diff`: the minimal correct fix.
   - `neg_<id>.diff`: a plausible wrong fix, of the same kind as the D1 LF-only control.
5. **Write the task texts, plus an allow-list per case.**
   - `task_<id>.md` states the user-visible symptom only, with no cause class and no file hint. It must be in the same style as D1 and D2.
   - `allow_<id>`: the paths the diff may touch, usually the package and `tests/`.
6. **Self-check every case,** run by the teacher using the harness under CPython 3.12, not the embeddable Python. For every one of the 6 cases, all of these must hold:
   - hv **FAIL on the base**;
   - hv **PASS on base + ref**;
   - hv **FAIL on base + neg**;
   - existing tests green on base + ref;
   - the ref diff stays within the allow-list;
   - a clean apply with `autocrlf=false`.
   Record the sha256 of every hv/ref/neg/task file.
7. **Freeze the manifest.** Update `MANIFEST.json` so every case is `READY` with its SHAs and a `frozen_at` timestamp set before any student attempt. Check that the student roots contain no hv, ref or neg file.
8. **Set the protocol for the run itself (tomorrow, after step 7).**
   - Train T1–T3: hint ladder L0 to L4; save lessons only from hv-verified student fixes.
   - Restart, then run holdouts H1–H3 at L0 in both profiles, RAW and LESSON_AVAILABLE.
   - Use n ≥ 3 repetitions per profile per holdout. With only n=1, a result like D2 (RAW already passing) stays NO_MEASURED_GAIN or INSUFFICIENT_EVIDENCE.
   - Label each result STUDENT_UNASSISTED_PASS, STUDENT_COACHED_PASS, TEACHER_PATCH or FAIL. Keep RUNTIME=OLLAMA_PROXY and WEIGHTS_UNCHANGED, and use the same identity files.

Nothing from exam3 was run today: no hv, no student attempt, no git writes.
