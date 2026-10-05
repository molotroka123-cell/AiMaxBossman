---
name: bossman-owner-run-lessons
description: Verified moves from the 2026-10-05 owner run — consolidating branches into one Bossman, installing a build, keeping one Jeff, free-only routes, security-fix pattern, and the traps that cost hours. Read before release/consolidation/install work.
compatibility: BOSSMAN (Windows owner machine), Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
  learned_from: owner run 2026-10-05 (goal/bossman-self-improvement-tree-20261005)
---

# Owner-run lessons (05.10.2026)

Each item: **sign → what to do → how to check**. These were done and verified, not planned.

## 1. One Bossman from many branches
- Sign: features live on several branches; owner asks for «один Bossman/Jeff».
- Do: map first (read-only `git merge-tree`, `git log goal..branch`), pick the SUPERSET branch, `git merge --no-ff` it, then
  `cherry-pick -x` the few fixes it lacks. Never merge a branch whose only diff is CRLF rewrites — re-apply the real lines (`git show --ignore-cr-at-eol`).
- Conflicts: keep BOTH behaviours; when two owner decisions disagree, the newer dated decision wins and the older test is updated with a comment.
- Check: the full suite of the touched area (Jeff: `tests/test_pit* test_jeff* test_j2* tests/telegram_calls`), not a sample.

## 2. Install = build → verify → Switch
- `git clone -c core.autocrlf=false <worktree> tree-build/<sha>/src` at the exact SHA → `tools/build_windows_bundle.py --out … --zip`
  → `tools/verify_windows_bundle.py --archive … --expected-sha …` (must print `BOSSMAN_BUNDLE_ACCEPTANCE=PASS`)
  → `pwsh tools/owner_one_bossman.ps1 -Action Switch -Sha <40> -Archive <zip> -ArchiveSha256 <sha256> -Evidence <dir>`.
- Check: `/health/live` → `build_sha`; exactly one backend, one `bcc.pit.cli start`, one `bcc.telegram_companion`.

## 3. Jeff routes: $0 only
- Jeff has a hard $0 ceiling (`pit/cloud_budget.py`). Owner rule: free cloud + local uncensored, never paid, never Claude.
- Rank free models live before adding (Russian prompt, latency, no reasoning leakage, no 403/429 forever). Edit
  `pit-v1.7/config.json` only after a dated backup; restart Jeff; `bcc.pit.cli doctor` → `free_route PASS`.
- Reject a model that prints its chain of thought to the participant (nemotron-3.5-lightning did).

## 4. Security-fix pattern
- Validate each audit candidate against CURRENT code (symbol names, not old line numbers). Fix minimal; add a test that
  FAILS on the old code (prove it by temporarily restoring the old line), then passes.
- Principle that found the worst bug: **approved == executed** — whatever normalizes args for approval must be what runs.

## 5. Traps (each cost real time)
- Windows console stdin is cp1251: sending Russian text through a script reading `sys.stdin` produced mojibake in Telegram.
  Read `sys.stdin.buffer` and decode UTF-8; set `PYTHONIOENCODING=utf-8`.
- A lone surrogate in model context → provider HTTP 400. Serialize with `ensure_ascii=False` + `encode("utf-8","replace")`.
- Files written by an elevated process get owner Administrators → the normal process cannot read them
  (`icacls <f> /setowner asd` + `/grant asd:F`). If still unreadable, the content itself changed — do not overwrite secrets; ask the owner.
- `PYTHONPATH` must include absolute `command-center;bossman-core;<repo>` or tests import an OLD installed `bossman_v3`.
- Git Bash eats backslashes and `/api/...` argv: use `MSYS_NO_PATHCONV=1` or Python for Windows paths.
- One GPU = one video generation (2 parallel sd-cli → "Vulkan device lost" killed both).
- A bare `/confirm` in the пульт now works for a single live proposal; with several, the code is still required.

## 6. Honesty rules that held
- A partial audit is not a PASS; an unmeasured speed is «по данным исследования», not a fact.
- Self-improvement counts only when a zone task is completed AND the independent check passes (leaf «проверено»);
  failures are reported as failures (tree shows them, пульт receives them).
