# Qwen apprentice scoreboard — live (2026-09-22)

Updated after every attempt. Verdicts come from HIDDEN verifiers (frozen before the student run)
+ GPT-OSS-120B review, never from the student's own claim. Status vocabulary:
FAST_ONLY · QWEN_UNASSISTED · QWEN_GPTOSS_REVIEWED · QWEN_GPTOSS_COACHED ·
CLAUDE_HINT_REQUIRED · CLAUDE_PATCH_REQUIRED · FAIL · HARNESS (attempt not scorable).

| Task | Attempt | Model | Result | Verdict | Why (evidence) | Training consequence |
|---|---|---|---|---|---|---|
| DESK-EDGE-RELAUNCH (P1) | a1 06:28Z | MAIN 27B, ctx 32k | crashed at turn 4 | HARNESS | `request 38480 > 32768 ctx` — server started with 32k | none (not the model's fault); ctx raised |
| DESK-EDGE-RELAUNCH (P1) | a2 06:36Z | MAIN 27B, 2×64k slots | crashed at turn 21, partial patch | HARNESS (not scored) | `request 66032 > 65536` per slot | observation only: partial patch detected the holder via `wmic` — `wmic` does not exist on this Windows 11 (verified), so it would silently fail-open. Kept as a candidate reviewer check, NOT a lesson (unverified attempt). |
| DESK-EDGE-RELAUNCH (P1) | a3 07:14Z | MAIN 27B, ctx 128k/1 slot | STOPPED by owner shutdown at 07:49Z, 38 API steps, uncommitted +167 lines (desktop.py + test_ux2_desktop.py) | NOT_SCORED | diff saved: owner-training/runs/desk-edge-a3-partial.diff. Integrator pre-read (not shown to student): holder detection only in the timeout branch; exact string match on --user-data-dir without path normalisation; waits up to max(5,timeout) when no browser exists (req. 2 wants a quick exit) | resume: score a3 diff with hidden verifier, then GPT-OSS review level 1 |
| MEDIA-RESTART (P1) | — | — | task frozen | pending | hidden verifier: base FAILS, teacher reference 2/2 (private) | — |

## Counters
QWEN unassisted 0 · GPTOSS-reviewed 0 · GPTOSS-coached 0 · CLAUDE hints 0 · CLAUDE patches 0 ·
GPT-OSS reviews 0 · verified lessons 0 · episodes 0.
