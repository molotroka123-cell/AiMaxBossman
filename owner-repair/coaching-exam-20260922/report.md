# Coaching exam coaching-exam-20260922 — run exam-20260922T190223Z

**Run status:** `LOCAL_EXAM_MEASURED`  
**Student backend:** local OpenAI-compatible http://127.0.0.1:8081 model=C:\Users\asd\Bossman\models\qwen38-27b\Qwen3.8-27B-UD-Q5_K_M.gguf (discovered: C:\Users\asd\Bossman\models\qwen38-27b\Qwen3.8-27B-UD-Q5_K_M.gguf)  
**Discovered model:** `C:\Users\asd\Bossman\models\qwen38-27b\Qwen3.8-27B-UD-Q5_K_M.gguf`  
**Base SHA:** `f30ed02c496f5ac622f216cfd376b353e1c0bd3b`  
**WEIGHTS_UNCHANGED: no fine-tuning, no weight update anywhere in this runner**

## Students

| role | endpoint | reachable | discovered model |
|---|---|---|---|
| MAIN | `http://127.0.0.1:8081/v1` | yes | `C:\Users\asd\Bossman\models\qwen38-27b\Qwen3.8-27B-UD-Q5_K_M.gguf` |
| FAST | `http://127.0.0.1:8082/v1` | yes | `C:\Users\asd\Bossman\models\qwen36-35b-a3b\Qwen3.6-35B-A3B-UD-Q5_K_M.gguf` |

## Per case

| student | case | split | profile | status | attempts | hints | teacher patch | memory hit | repeated | s |
|---|---|---|---|---|---|---|---|---|---|---|
| MAIN | TRAIN-1 | train | no_lessons | **TEACHER_PATCH** | 4 | 3 | yes | None | 1 | 590.28 |
| MAIN | TRAIN-2 | train | no_lessons | **TEACHER_PATCH** | 4 | 3 | yes | None | 3 | 405.61 |
| MAIN | HOLDOUT-1 | holdout | no_lessons | **STUDENT_UNASSISTED_PASS** | 1 | 0 | no | None | 0 | 93.31 |
| MAIN | HOLDOUT-2 | holdout | no_lessons | **FAIL** | 4 | 0 | no | None | 0 | 612.97 |
| MAIN | TRAIN-1 | train | with_lessons | **TEACHER_PATCH** | 4 | 3 | yes | True | 2 | 679.33 |
| MAIN | TRAIN-2 | train | with_lessons | **TEACHER_PATCH** | 4 | 3 | yes | True | 3 | 548.95 |
| MAIN | HOLDOUT-1 | holdout | with_lessons | **STUDENT_UNASSISTED_PASS** | 1 | 0 | no | None | 0 | 72.26 |
| MAIN | HOLDOUT-2 | holdout | with_lessons | **FAIL** | 4 | 0 | no | None | 1 | 531.62 |
| FAST | TRAIN-1 | train | no_lessons | **TEACHER_PATCH** | 4 | 3 | yes | None | 1 | 163.02 |
| FAST | TRAIN-2 | train | no_lessons | **TEACHER_PATCH** | 4 | 3 | yes | None | 3 | 129.14 |
| FAST | HOLDOUT-1 | holdout | no_lessons | **STUDENT_UNASSISTED_PASS** | 2 | 0 | no | None | 0 | 52.34 |
| FAST | HOLDOUT-2 | holdout | no_lessons | **FAIL** | 4 | 0 | no | None | 2 | 135.61 |
| FAST | TRAIN-1 | train | with_lessons | **TEACHER_PATCH** | 4 | 3 | yes | True | 1 | 184.47 |
| FAST | TRAIN-2 | train | with_lessons | **TEACHER_PATCH** | 4 | 3 | yes | True | 3 | 124.5 |
| FAST | HOLDOUT-1 | holdout | with_lessons | **STUDENT_UNASSISTED_PASS** | 1 | 0 | no | None | 0 | 25.77 |
| FAST | HOLDOUT-2 | holdout | with_lessons | **FAIL** | 4 | 0 | no | None | 0 | 136.19 |

## Rates (unassisted / coached / teacher patch are never merged)

| student/profile/split | cases | unassisted | coached | teacher patch | fail | hints | memory ok | s |
|---|---|---|---|---|---|---|---|---|
| FAST/no_lessons/train | 2 | 0.0 | 0.0 | 1.0 | 0.0 | 6 | None | 292.16 |
| FAST/no_lessons/holdout | 2 | 0.5 | 0.0 | 0.0 | 0.5 | 0 | None | 187.95 |
| FAST/no_lessons/all | 4 | 0.25 | 0.0 | 0.5 | 0.25 | 6 | None | 480.11 |
| FAST/with_lessons/train | 2 | 0.0 | 0.0 | 1.0 | 0.0 | 6 | 1.0 | 308.97 |
| FAST/with_lessons/holdout | 2 | 0.5 | 0.0 | 0.0 | 0.5 | 0 | None | 161.96 |
| FAST/with_lessons/all | 4 | 0.25 | 0.0 | 0.5 | 0.25 | 6 | 1.0 | 470.93 |
| MAIN/no_lessons/train | 2 | 0.0 | 0.0 | 1.0 | 0.0 | 6 | None | 995.89 |
| MAIN/no_lessons/holdout | 2 | 0.5 | 0.0 | 0.0 | 0.5 | 0 | None | 706.28 |
| MAIN/no_lessons/all | 4 | 0.25 | 0.0 | 0.5 | 0.25 | 6 | None | 1702.17 |
| MAIN/with_lessons/train | 2 | 0.0 | 0.0 | 1.0 | 0.0 | 6 | 1.0 | 1228.28 |
| MAIN/with_lessons/holdout | 2 | 0.5 | 0.0 | 0.0 | 0.5 | 0 | None | 603.88 |
| MAIN/with_lessons/all | 4 | 0.25 | 0.0 | 0.5 | 0.25 | 6 | 1.0 | 1832.16 |

## Holdout isolation

- sealed directory: `C:\Users\asd\Bossman\exam-sealed-20260922` (outside every git worktree)
- sealed files hash-checked: 23, mismatches: 0
- holdout case ids: HOLDOUT-1, HOLDOUT-2
- lessons refused because they belong to a holdout case: LSN-TG-ENV-CONCAT
- holdout verifier output in this report: REDACTED to a verdict
- lessons written from holdout cases: 0 (must be 0)

## Lessons

- seeded and verified: 2 (LSN-MEDIA-EN-ENCODER, LSN-STUDIO-FIRST-LIST-TIMEOUT)
- rejected by the poison filter: 0

`WEIGHTS_UNCHANGED: no fine-tuning, no weight update anywhere in this runner`
