# Coaching exam coaching-exam-20260922 — run exam-20260922T181501Z

**Run status:** `MOCK`  
**Student backend:** MOCK scripted student (smart) — not a model, not a measurement  
**Discovered model:** `mock:smart`  
**Base SHA:** `f30ed02c496f5ac622f216cfd376b353e1c0bd3b`  
**WEIGHTS_UNCHANGED: no fine-tuning, no weight update anywhere in this runner**

> MOCK RUN — every number comes from a scripted student. It proves the harness, not the ability of any model.

## Students

| role | endpoint | reachable | discovered model |
|---|---|---|---|
| MOCK | `None` | yes | `mock:smart` |

## Per case

| student | case | split | profile | status | attempts | hints | teacher patch | memory hit | repeated | s |
|---|---|---|---|---|---|---|---|---|---|---|
| MOCK | TRAIN-1 | train | no_lessons | **STUDENT_UNASSISTED_PASS** | 1 | 0 | no | None | 0 | 9.7 |
| MOCK | HOLDOUT-1 | holdout | no_lessons | **FAIL** | 4 | 0 | no | None | 3 | 1.69 |
| MOCK | TRAIN-1 | train | with_lessons | **STUDENT_UNASSISTED_PASS** | 1 | 0 | no | False | 0 | 6.95 |
| MOCK | HOLDOUT-1 | holdout | with_lessons | **FAIL** | 4 | 0 | no | None | 3 | 1.61 |

## Rates (unassisted / coached / teacher patch are never merged)

| student/profile/split | cases | unassisted | coached | teacher patch | fail | hints | memory ok | s |
|---|---|---|---|---|---|---|---|---|
| MOCK/no_lessons/train | 1 | 1.0 | 0.0 | 0.0 | 0.0 | 0 | None | 9.7 |
| MOCK/no_lessons/holdout | 1 | 0.0 | 0.0 | 0.0 | 1.0 | 0 | None | 1.69 |
| MOCK/no_lessons/all | 2 | 0.5 | 0.0 | 0.0 | 0.5 | 0 | None | 11.39 |
| MOCK/with_lessons/train | 1 | 1.0 | 0.0 | 0.0 | 0.0 | 0 | 0.0 | 6.95 |
| MOCK/with_lessons/holdout | 1 | 0.0 | 0.0 | 0.0 | 1.0 | 0 | None | 1.61 |
| MOCK/with_lessons/all | 2 | 0.5 | 0.0 | 0.0 | 0.5 | 0 | 0.0 | 8.56 |

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
