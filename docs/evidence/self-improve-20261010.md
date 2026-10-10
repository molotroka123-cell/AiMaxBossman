# Self-improvement run 10.10: CI of the release line, coded by cheap models

Owner request (10.10): «используй Mistral glm 5,3 flash и haiko 5,5 пусть они само улучшают боссман».
Goal: turn the red CI of PR #107 (head `a1564fef`, `claude/main-prod-20261009` → `main`) green.

- **Coders:** GLM 5.3 Flash (`z-ai/glm-5.3-flash` via OpenRouter; there is no `:free` GLM Flash) and Mistral Large (`mistral-large-latest`, owner key from `bcc.db` providers id 10, decrypted in-process, never printed).
- **Reviewer:** Haiku 5.5 (`anthropic/claude-haiku-5.5`).
- **Verifier:** Claude Opus 5.5. It decided whether each diff was accepted, applied it, ran the tests and wrote this file.
- **Branch:** `fix/ci-green-20261010` from `ux/genjutsu-live-constructor` (`7db5facf`, which contains `a1564fef` plus 10 tree/docs commits). The branch is not pushed or merged.

## How the failures were reproduced

Job logs need GitHub auth, which was not available, so only the job and step names came from the public API:

| CI job (run) | Failing step |
|---|---|
| root pytest + hygiene py3.11 / py3.12 (root-ci, push and PR) | `Root suite …` (`python -m pytest tests …`) |
| browser-user-paths (editors-user-safety) | `Registry, secrets and unchanged source` |
| measured intelligence retention | `Fetch owner-attested redacted evidence for this commit` |

Bisecting with the public API: the last green root-ci run was at `6273524c`, and the first red run was at `2a77afba`. Between them there is one commit, which touched only `command-center/` and added `command-center/tests/test_direct_gen_faceswap.py` with 4 skips.

The suite was then run locally in a venv that matches CI exactly (`pip install -e .` plus `pytest pytest-timeout psutil httpx pyyaml`). The shared `venv-verify-20261009` could not be used for this: its editable installs point at another checkout (`wt-main-prod-20261009`).

## Failures, causes, fixes

| # | Failure | Where | Root cause | Fix | Written by | Result |
|---|---|---|---|---|---|---|
| 1 | `tests/test_skips_registry.py::test_registry_is_current…` and `tools/skips_registry.py --check` (`SKIPS_REGISTRY_CURRENT=FAIL`) | **CI**: root-ci both Pythons, and browser-user-paths | The 4 new skips from `2a77afba` were never regenerated into `docs/testing/SKIPS_REGISTRY.md` (426 → 430 rows) | Ran `python tools/skips_registry.py` (generator output, LF) | Both GLM and Mistral named the right cause and command. **Both hand-written diffs were wrong** (wrong file position and wrong BFS order: line 355 comes before 175/183/184), so they were rejected. Haiku's review caught the same errors. The verifier ran the command they named. | `--check` PASS. The 4 editors browser files passed locally: 14 tests, 0 skipped, 0 failures, `git diff --exit-code` clean |
| 2 | `tests/test_operator_step_profile.py::test_phase_timing…` (`16.0 >= 28.5`) | Owner Windows only (Linux CI green) | `time.monotonic()` on Windows/Py3.12 is `GetTickCount64` with a 15.625 ms resolution, so a 5 ms phase was recorded as 0 | `_bounded` measures duration with `perf_counter`; the deadline stays on `monotonic` | **GLM 5.3 Flash** had the right fix, but its hunk header was malformed. The verifier applied the identical 2 lines by hand. Haiku: right fix, malformed diff (correct). | 8/8 ×3, 116 core operator tests pass |
| 3 | `tests/test_ux_sweep_probe.py` × 2: `ModuleNotFoundError: playwright` | Owner Windows only (Edge present, thin venv) | The tests were guarded only by "Edge installed" | Per-test `pytest.importorskip("playwright", reason=…)`, then the registry was regenerated (432 rows) | **GLM 5.3 Flash** (applied with `git apply --recount`). **Mistral rejected**: it put a module-level `importorskip` in the middle of the file, which would have skipped all 13 tests. | Thin venv: 11 passed, 2 skipped with a reason. Full venv with playwright: 10/10 passed, so the browser tests still run. |
| 4 | `tests/test_shipped_runners_console.py::…hardware_runner…` timed out after 300 s | Owner PC only: it **is** the target hardware | On the target host, `main()` really runs 4 on-target checks of up to 1800 s each | The test driver stubs `run_on_target` in `main.__globals__` with a canned Cyrillic result; `-I`, cp1252 and the assertions are unchanged | **GLM 5.3 Flash, attempt 2.** Attempt 1 followed a **wrong hint from the verifier** (it stubbed the copy of globals that `run_path` returns) and was rejected after a measurement. Haiku rejected attempt 2 over a hunk header, but a strict `git apply --check` passed, so the verifier overrode that review. | 37/37 in 2 s. Negative control: with `utf8_console()` disabled, the test still fails with `UnicodeEncodeError`. |

No test was weakened, no xfail was added, and nothing was skipped except a missing package, which is listed in the registry.

## Left red

- **measured intelligence retention:** needs owner-attested redacted evidence for the exact head SHA (`tools/intelligence_evidence_transport.py fetch --expect-sha`). This is an owner action, and it was deliberately not faked. It also has to be redone for the new head SHA once this branch is integrated.
- The CI result for this branch is unknown until it is pushed. The local evidence is only from Windows; the Linux-only behaviour above is reasoned from the workflow and code, not observed.

## Final local root suite (CI-identical venv, Windows, Py 3.12.10)

`python -m pytest tests -q --timeout=600 --timeout-method=thread` (the CI command, but `signal` is not available on Windows):
**3087 passed, 30 skipped, 0 failed** in 16 min 40 s. Before the fixes, the same command gave 4 failed + registry red.
Hygiene steps run locally: skips registry PASS, README scorecard PASS, compileall OK, secret scan PASS, `git diff --check` clean.

## Spend (from the OpenRouter `usage.cost` ledger)

| Model | Calls | Tokens in/out | Cost |
|---|---|---|---|
| GLM 5.3 Flash | 6 (1 empty: reasoning ate `max_tokens`) | 16 572 / 9 416 | $0.0067 |
| Haiku 5.5 | 4 (1 truncated) | 20 206 / 9 572 | $0.0068 |
| Mistral Large | 2 | 4 266 / 1 647 | ≈ $0.005, estimated from list price (the API returns no cost) |

Total ≈ **$0.018**, well under the caps (Haiku $0.50, GLM $1, Mistral €2).

## Scorecard: who was right

- **GLM 5.3 Flash:** 4 of 4 root causes right. 3 accepted fixes (after one round of verifier feedback on #4). 2 of 4 diffs were malformed or hand-wrong.
- **Mistral Large:** 1 of 2 root causes right. 0 accepted diffs (2 rejected, one of which would have weakened the suite).
- **Haiku 5.5 reviewer:** 2 correct rejections (#1 wrong diff, #2 malformed header). 1 false rejection (#4, disproved by `git apply`).
- **Verifier:** gave one wrong hint (#4 attempt 1), caught by measurement before anything was applied. The verifier wrote no product logic. It ran the generator (#1) and hand-applied GLM's exact 2-line change (#2).
