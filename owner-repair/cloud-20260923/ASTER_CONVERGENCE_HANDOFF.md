# Aster: cloud convergence handoff, 2026-09-23

Branch: `claude/bossman-cloud-closure-owner-a6s1ki`, PR #74. The final candidate is the branch HEAD **after** this handoff commit; pin its complete SHA from GitHub before running any gate. No main promotion or Windows certification has been claimed.

## Merged onto the branch

| Surface | Actual state | Verification performed here |
|---|---|---|
| Evolution CLI | `tools/bossman_evolve.py` routes loop/status/pause/resume/stop and campaign report to the existing bounded engine; config and suite shipped in Windows app-support | isolated CLI/bundle unit tests passed earlier in cloud |
| Evolution API | `command-center/bcc/features/evolution.py` uses one Command Center, one campaign, code-root checks and the same worker; no stable auto-promotion | isolated bootstrap test passed; full HTTP test **NOT_RUN** (fastapi/uvicorn unavailable here) |
| Telegram | six `/evolution_*` controls in the existing companion/control lane, owner plus `pc_control`, authenticated Core adapter | compileall passed; HTTP contract test **NOT_RUN** (httpx unavailable here) |
| Terminal lock | universal `prompt_toolkit` 3.0.52 and `wcwidth` 0.8.4 wheels pinned in Windows lock; existing Terminal launchers preserved | lock and skip registry tests passed earlier; installed CMD test **NOT_RUN** |
| Bossfield | gated Seedance 2.5 Studio route, unverified disabled catalog entry, 30s editing helper, reference photo and owner runbook copied into this branch; installed helper goes in `app-support` | isolated compileall and catalog uniqueness/gating passed; paid API, AMD generation, installed ZIP **NOT_RUN** |
| Local model bake-off | three pinned candidates, TTFT/prefill and process RSS instrumentation, all measurements optional | unit checks passed earlier; owner VRAM and real quality **UNMEASURED** |

## Mandatory red / unproven gates

- Exact-SHA Windows ZIP and installed verification: **BLOCKED on this Linux host**. `python -I tools/build_windows_bundle.py --zip` returns `BOSSMAN_WINDOWS_BUNDLE=UNSUPPORTED_HOST`; use the Windows CI artifact from the exact frozen SHA, unpack outside checkout, run `tools/verify_windows_bundle.py` and actual CMD/chat.
- The Windows verifier now fails when Bossfield is absent or its installed `app-support/bossfield_owner_run.py --help` fails under embedded `python -I`. Linux regression `PYTHONPATH=tools python -m pytest -q tests/test_windows_bundle_contract.py` passed (56 tests). This is verifier wiring, **not** evidence that the Windows ZIP passed.
- CI root/core/Command Center/Windows: GitHub workflows for recent commits are queued/pending or cancelled when superseded. Wait for all required checks for exactly the frozen SHA before changing `main`.
- GitHub Actions diagnosis: the cloud commit showed jobs queued with no steps/logs; Command Center was pending with no jobs. Several workflows use `concurrency.cancel-in-progress: false`, which can leave a newer SHA pending while another run holds its group. The API evidence does not establish whether runner capacity, account quota or a repository setting is the cause of the remaining queued jobs. Do not remove required jobs or weaken gates to clear the queue; check the Actions UI/usage/runners outside this sandbox.
- Three-cycle evolution gate, hard-kill resume, regression, rejected bad patch, independent verifier and 24/48-hour soak: **NOT PASSED**. The draft `wip/evolution_gate_UNTESTED.py.txt` remains deliberately uninstalled: the local smoke failed before Command Center boot because `uvicorn` is unavailable here. Do not present deterministic `MOCK_MODEL` plumbing as a real-model pass.
- The real self-repair transfer and memory retention, owner Strix Halo measurements, OpenRouter paid video/API tests, and 30-second continuity review require owner hardware and credentials; status **OWNER_REQUIRED**.
- Community-evals candidate and remaining Terminal end-to-end command/screen checks remain open; do not infer their success from CLI compilation.

## Next safe actions for Claude on the frozen SHA

1. Fetch and verify exact branch HEAD; do not rebase or force-push. Read `ASTER_MASTER_PROMPT.md` and `docs/bossfield/BOSSFIELD_CLAUDE_MERGE_AND_OWNER_RUN_RU.md`.
2. Run required Linux and Windows workflows on the same SHA. Reproduce each red before fixing it; each fix yields a new SHA and restarts the artifact/CI gate.
3. On Windows build/download the ZIP for that SHA, verify SHA-256 and the installed application outside the checkout. Inspect actual Terminal CMD/chat, companion HTTP commands, Evolution loop and STOP after restart.
4. Preserve the old `main` via backup ref before any attempted ordinary fast-forward. Advance `main` only with every mandatory gate green and that artifact exact-SHA; otherwise report `MAIN_PROMOTED=NO` and the blocker.
