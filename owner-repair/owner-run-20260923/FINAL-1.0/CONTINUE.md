# CONTINUE — Bossman 1.0 (start here, do NOT re-audit)

State at end of 23.09: ONE line `fix/owner-run-20260923-p1` = `f7ff326a` (evening: + media fixes 6a2b5907/99970958, Studio vision review d7824c9d/b43c7b92/f7ff326a (session e2), Telegram web search bed3dbb4, /jev 05ca8d22, SKIPS_REGISTRY regen 5ca2ce35, retention fix 9c4afd08; independent verifier PASS on d7824c9d; ZIP 05ca8d22 acceptance PASS sha256 4ba2b809…; ZIP f7ff326a building) on `fix/owner-run-20260923-p1` (pushed). Truth table: `FINAL-1.0/EOD/20MIN_CLOSURE.md`. Audit: `EOD/EOD_FINAL_AUDIT.md`. release/bossman-owner untouched (`e0bf948d`, ancestor → fast-forward later).

## FIRST COMMAND TOMORROW
```
cd C:\Users\asd\Bossman\wt-fix-crlf0923 && git fetch --all --prune && git status -sb && git log -1 --format=%H
```
Expect `f7ff326a…` (or newer if the ZIP/evidence pass added commits) and a clean tree, then do step 1 directly.

## Order (max 5 steps, no new features, no new final branch)
1. (DONE 6f9d1497/1a29d85a) C2 + C3 (reproducers in EOD/SECURITY notes of 20MIN_CLOSURE): opencode — compute target and check roots BEFORE `git worktree add`, validate name; terminal roots — require list of existing absolute dirs, refuse drive/filesystem root without approval. Failing test → fix → neighbours (test_*opencode*, test_*terminal*) → push.
2. Independent re-attack on the head (not by the fix authors): containment (../, sibling prefix, junction), Computer Use semantic/approval, sandbox destructive, approval replay/STOP. Then full regression with `build-0923\regress\run_full.sh <src> <label>` (root/core without command-center on PYTHONPATH) and classify the SHA4 failures listed in `FINAL-1.0/regress/`.
3. Windows-100: `scripts/windows_stress_100.py` (plan: health 8, auth 16, memory 10, tasks 14, approvals 15, containment 18, CU STOP 4, browser 4, concurrency 5, restart 5, 1 NOT_RUN), workflow with a NEW display name, add to `tools/exact_sha_certify.DEFAULT_REQUIRED`; install from `git clone`, not `git archive`.
4. Declare RC in `tools/release_candidate.json` → push the exact SHA to a `claude/**` CI-trigger ref (4 required workflows only run on claude/night/release pushes) → `python tools/exact_sha_certify.py --sha <SHA> --fetch --repo molotroka123-cell/AiMaxBossman` → build ZIP from that SHA (`tools/build_windows_bundle.py --zip --profile release`), verify embedded SHA, SHA-256; live retest on the unpacked ZIP: HW-02, PDF download, coding full repo + apply, TR smoke, standard-user, Jev shadow.
5. Independent final audit → fast-forward release/bossman-owner to the certified SHA, check tree equality.

## Do NOT tomorrow
new final branches · architecture rewrites · repeating proven tests · features before 1.0 freeze · starting over · counting the default-branch "Windows 100" print-loop.

## Owner decisions pending
- Retire the fake `windows-stress-100.yml` on the default branch.
- HW-06/TR-12: allow stopping the Claude Telegram inbox poller and running the test companion (`C:\Users\asd\Bossman Test 0923\tg-companion\config.json`, secrets only via env) for a live Telegram test.

## Learning (tomorrow's benchmark, not before steps 1–5 are green)
exam3 cases in `C:\Users\asd\Bossman\exam-sealed-0923\exam3\` are INCOMPLETE — finish per `EOD/LEARNING_EOD.md`, self-check, freeze, then ≥3 repeats per profile.

## Evening additions (23.09)
- Windows-100: taken by session bossman-e2 on feat/windows-stress-100-20260923 (not started today) — merge into the line when its run on the installed product is green.
- CI on f7ff326a: root-ci expected green now (SKIPS_REGISTRY regenerated); 5 required workflows cannot start on fix/* (branch filters) → exact-SHA needs a claude/** trigger ref or release push after final audit.
- Open from triage: Windows coarse clock makes v3 receipts 'stale' (bossman_shared/action_receipt.py, test_v3_cross_layer_e2e / test_v3_fence_receipts) — PRODUCT, OPEN; 3 cc tests cause not read (openrouter_agent_flow env_configured, video_cancel_button, video_descriptor_boundary).
- 1.5: NOT started — master prompt requires frozen 1.0 first (EOD/V1_5_BASELINE.md).
