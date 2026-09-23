# Coaching run OR0923-coach-main

**Status:** `LOCAL_LEARNING_GAIN_MEASURED`  
**Backend:** local OpenAI-compatible endpoint http://127.0.0.1:11500 model=bossman-main-qwen38-27b-q5:latest  
**WEIGHTS_UNCHANGED: no fine-tuning, no weight update anywhere in this runner**

| profile/split | tasks | pass@1 | student pass (any) | teacher passes (not student) | attempts | tool errors | interventions | lessons in | lessons written | wall s |
|---|---|---|---|---|---|---|---|---|---|---|
| unassisted/train | 5 | 1.0 | 1.0 | 0 | 5 | 0 | 0 | 0 | 0 | 21.11 |
| unassisted/holdout | 5 | 1.0 | 1.0 | 0 | 5 | 0 | 0 | 0 | 0 | 25.687 |
| unassisted/all | 10 | 1.0 | 1.0 | 0 | 10 | 0 | 0 | 0 | 0 | 46.797 |
| teacher_patch/train | 5 | 1.0 | 1.0 | 0 | 5 | 0 | 0 | 0 | 0 | 20.188 |
| teacher_patch/all | 5 | 1.0 | 1.0 | 0 | 5 | 0 | 0 | 0 | 0 | 20.188 |
| coached/train | 5 | 1.0 | 1.0 | 0 | 5 | 0 | 0 | 0 | 0 | 20.0 |
| coached/holdout | 5 | 1.0 | 1.0 | 0 | 5 | 0 | 0 | 0 | 0 | 24.656 |
| coached/all | 10 | 1.0 | 1.0 | 0 | 10 | 0 | 0 | 0 | 0 | 44.656 |

Holdout pass@1: unassisted=1.0 coached=1.0 delta=0.0 (measured on the configured local endpoint)
Lessons in store at startup: 0; after run: 0 (verified 0); rejected at write: 0; filtered at read: 0
