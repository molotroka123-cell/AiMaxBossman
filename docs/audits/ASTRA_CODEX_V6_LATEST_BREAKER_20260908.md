# ASTRA / CODEX: V6 to latest breaker audit

TESTED_BASE_SHA = 7b4ab7657291ccf4f074c00fa64d8d4674cb67ca
TESTED_HEAD_SHA = 45027d3e9aef554407a0a9ff07fb8678d1b849f3
P0_COUNT = 0
P1_COUNT = 1
OTHER_REPRODUCIBLE_REGRESSIONS = 1
FALSE_PASS_EVIDENCE = F1, F3, F5
UNRESOLVED_REPO_FIXABLE = 7
FREEZE_VERDICT = BLOCKED

No real P0 reproduced. One P1 reproduced twice. No production files modified.
Scope: fetched V6 baseline and night/V7 convergence branches, inspected their diff/history and selected changed boundaries only.
Initial pass started 2026-09-08 18:19 UTC and stopped early on owner's instruction to push quickly.
Owner authorized a final continuation; finalized at 18:40 UTC following another instruction to push available evidence.
Tested production tree remains exactly the HEAD above; `git ls-remote origin refs/heads/night/v7-convergence-20260908` reconfirmed it at finalization.
Independent worktree initially `C:/astra-breaker-20260908`, later moved to `<original-workspace>/artifacts/astra_codex_breaker_20260908/checkout` after access restrictions changed. Original dirty checkout files untouched.
Environment: Windows, Python 3.14.3, pytest 9.0.2. Initial isolated tests used no live provider credentials.
Skills used: differential-review and proof-before-done. Variant-analysis was read after F1; expansion was not performed before the owner's stop instruction.

## Exact reproduction commands

Run at the tested checkout root. `A` below means `artifacts/astra_codex_breaker_20260908`.
The supplied test file is evidence, not a production change. Fixture repositories are disposable and under A.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH="$PWD/command-center;$PWD/bossman-core;$PWD"
$env:TEMP="$PWD/artifacts/astra_codex_breaker_20260908"
$env:TMP=$env:TEMP
python -m pytest -o addopts='' -o asyncio_mode=auto -p no:cacheprovider artifacts/astra_codex_breaker_20260908/test_breaker.py -vs --basetemp=artifacts/astra_codex_breaker_20260908/repro-tmp
```

This is command R1: four failing invocations, with actual outputs in `A/adversarial.log`.
The two parameterized F1 invocations use separate clean repositories.

```powershell
python -m pytest -o addopts='' -o asyncio_mode=auto -p no:cacheprovider bossman-core/tests/apprentice/test_openhands_path_boundary.py bossman-core/tests/apprentice/test_worktree_client_acceptance.py bossman-core/tests/apprentice/test_openhands_client.py tests/test_trader_apprentice.py tests/test_v7_world_state_contested.py -q --basetemp=artifacts/astra_codex_breaker_20260908/core-tmp
```

Command R2: 80 passed, 1 failed in 29.68 seconds; `A/core-suite.log`.

```powershell
python -m pytest -c command-center/pyproject.toml -o addopts='' -p no:cacheprovider command-center/tests/test_streaming_contract.py -q --basetemp=artifacts/astra_codex_breaker_20260908/stream-tmp
```

Command R3: 30 passed in 0.14 seconds; `A/stream-suite.log`.

## Findings, prioritized

### F1 — P1: protected-file mutation admitted with empty evidence

- SHA tested: `45027d3e9aef554407a0a9ff07fb8678d1b849f3`.
- Exact test: R1, `test_protected_file_hidden_by_index_flag[1]` and `[2]`.
- Boundary: `bossman-core/bossman/apprentice/openhands_client.py`, `_changed_files`, `_validate_scope`, `OpenHandsClient.run`.
- Actor/precondition: untrusted configured sidecar; clean local repository without remotes; protected tracked file explicitly excluded from allowed paths.
- Sequence: sidecar runs `git update-index --assume-unchanged protected.txt`, writes protected.txt, returns completed JSON. HEAD and git config remain unchanged.
- Expected: refuse the result because the sidecar changed a protected file, regardless of index flags.
- Actual: protected contents become `unauthorized change\n`; result is `status=completed`, `changed_files=()`, `diff=''`.
- Reproducibility: 2/2 independent runs. Repository-fixable: YES.
- Impact: the evidence admission boundary trusts an index controlled by the untrusted producer; reviewer receives false empty evidence for a protected change.
- Scope qualification: reproduced inside the disposable repository; no owner-file damage or automatic merge demonstrated. Not classified P0. Introduction date not established; the hardened boundary remains bypassable at HEAD.
- Narrow remediation direction: derive protected-file evidence independently of sidecar-controlled index flags.

### F2 — P2 regression: asynchronous stream cap does not bound reading

- SHA tested: `45027d3e9aef554407a0a9ff07fb8678d1b849f3`.
- Exact test: R1, `test_async_max_chunks_really_bounds_consumption`.
- Boundary: `command-center/bcc/streaming.py:read_stream`; caller `OpenRouterClient.stream_outcome`.
- Expected: `max_chunks=2` stops consuming the provider after two content chunks.
- Actual: consumes all 100 input frames and only then returns two deltas.
- Reproducibility: 1/1. Repository-fixable: YES.
- Regression evidence: `git show 7b4ab7657291ccf4f074c00fa64d8d4674cb67ca:command-center/bcc/v2/openrouter_ext.py` has `if len(deltas) >= max_chunks: break` inside the network-reading loop. HEAD buffers the entire iterator before `parse_frames` applies the cap.
- Impact measured: excess input consumption despite caller cap. No infinite-provider/OOM claim made.
- Narrow remediation direction: enforce the limit while consuming the asynchronous stream.

### F3 — P2 false success: malformed truncated stream classified usable

- SHA tested: `45027d3e9aef554407a0a9ff07fb8678d1b849f3`.
- Exact test: R1, `test_partial_malformed_stream_is_not_a_usable_completed_answer`.
- Boundary: real `OpenRouterClient.stream_outcome`, HTTP mock transport, canonical parser.
- Input: HTTP 200, one content delta `partial`, then `{broken-json`, EOF; no finish reason or DONE event.
- Expected: incomplete malformed response does not receive a usable-answer verdict.
- Actual: `status=stream_degraded`, `ok=True`, `finish_reason=None`, detail records the non-JSON frame.
- Reproducibility: 1/1. Repository-fixable: YES.
- Impact: stream outcome advertises usable content despite observed corruption/truncation. Not evidence of completed mission or monetary effect. Baseline regression not asserted.
- Narrow remediation direction: distinguish partial content from successfully completed response.

### F4 — P2: Windows sandbox cleanup reports no error but leaves checkout

- SHA tested: `45027d3e9aef554407a0a9ff07fb8678d1b849f3`.
- Exact test: R2, `test_worktree_client_acceptance.py::test_cleanup_removes_the_sandbox_from_disk`.
- Boundary: changed `IsolatedWorktree` clone/cleanup lifecycle.
- Expected: sandbox path no longer exists after `cleanup()`.
- Actual: `assert not path.exists()` fails; path remains under A. `_cleanup` uses `shutil.rmtree(..., ignore_errors=True)`.
- Reproducibility: 1/1. Repository-fixable: YES. Baseline regression not experimentally established.
- Impact measured: disposable clone not removed on this Windows host. Disk exhaustion/data exposure not claimed.
- Narrow remediation direction: handle Windows deletion failures and verify removal before discarding the root reference.

## Coverage and explicit limits

R2 completed: OpenHands traversal/protected-path controls, symlinks, untracked-file evidence, remote/config/HEAD rejection, clone lifecycle; Trader Apprentice; contested/stale world-state tests. 80 passes and F4 failure.
R3 completed: malformed/empty stream fixtures, duplicate usage, recorded GLM shapes, HTTP provider errors, capability-probe classifications. 30 passes. These do not invalidate independent F2/F3 reproductions.
Symlink containment is explicitly not promised by the path-boundary tests; no additional containment finding inferred from their permissive assertion.

The broader selected command was started with `-c command-center/pyproject.toml -o addopts='' -p no:cacheprovider -q` and the following files:

```text
command-center/tests/test_authorization_at_effect_time.py
command-center/tests/test_p0_review_deadlock.py
command-center/tests/test_approval_scope.py
command-center/tests/test_policy_algebra.py
command-center/tests/test_mission_budget.py
command-center/tests/test_model_health.py
command-center/tests/test_streaming_contract.py
command-center/tests/test_apps_control.py
command-center/tests/test_trading_lab_unwired.py
command-center/tests/test_v7_reality.py
command-center/tests/test_v7_resource_pressure_edges.py
command-center/tests/test_v7_resource_aware_strategy.py
command-center/tests/test_v7_world_runtime.py
command-center/tests/test_browser_help_lookup.py
```

It used `--basetemp=C:/astra-breaker-20260908/artifacts/astra_codex_breaker_20260908/suite-tmp`.
`A/scoped-suite.log` contains progress through 34%, without a terminal summary; classify the run INCOMPLETE, never PASS. The execution session was no longer available when finalizing.
It targeted orphan waiting_approval, immediate revocation, DENY precedence, duplicate consumption, review loops, token loops, silent model health, Apps repetition, missing Trading Lab imports, resource-memory admission and V7 recovery.
Individual completion of those attacks is not claimed from progress dots.
NOT_RUN to completion: provider timeout plus fallback integration; Video/Web missing-post-state attack; browser/desktop runtime; live OpenRouter/GLM; real OpenHands SDK; remaining selected suite assertions.
Initial harness attempt failed because the audit temp parent directory did not exist; rerun created it. No production defect counted for that setup error.

Freeze is BLOCKED by F1. No full-repository certification, live-provider PASS, or stale external evidence is claimed.
Publication includes only this report, two reproducers and minimal evidence logs; temporary fixture repositories are excluded from the commit.

## Final continuation: two more reproduced defects

Command R4, from the same tested checkout with the environment in R1:

```powershell
python -m pytest -o addopts='' -o asyncio_mode=auto -p no:cacheprovider --timeout=30 artifacts/astra_codex_breaker_20260908/test_final_boundaries.py -vs --tb=short --basetemp=artifacts/astra_codex_breaker_20260908/final-attack-tmp
```

Result: 4 failed, 1 passed in 11.12 seconds; exact outputs in `A/final-attack.log`.
The extra incomplete suite log `A/final-suite.log` records progress to 37%, no final summary. It is not PASS evidence.

### F5 — P2: HTTP route neutralizes invalid memory requirements

- SHA tested: `45027d3e9aef554407a0a9ff07fb8678d1b849f3` (audit commits add no production differences).
- Exact tests: R4, `test_invalid_memory_requirement_not_admitted[nan]` and `[-1024]`.
- Boundary: `command-center/bcc/features/reality.py:strategies` calls the hardened strategy generator after applying `max(0.0, float(value))`.
- Input: real authenticated ASGI GET `/api/reality/strategies?large_model_mb=nan`, then the same request with `-1024`; no memory observation exists.
- Expected: reject invalid requirement or exclude the large-model candidate.
- Actual: both return HTTP 200, `memory.measured=False`, `available_mb=None`, and `large-model-tools` in ranked candidates.
- Reproducibility: 1/1 per input, 2/2 variants. Repository-fixable: YES.
- Severity limit: this endpoint is advisory; no model allocation or execution observed. Not P1.
- False-PASS evidence: final-candidate resource claim includes NaN/negative fail-closed coverage, but route-level coercion defeats the generator's validation. Existing pure-generator checks do not cover this input path.
- Narrow remediation direction: validate before coercion; preserve invalid inputs as rejection rather than zero memory cost.

### F6 — P2: alternating failure classes replenish recovery rungs

- SHA tested: `45027d3e9aef554407a0a9ff07fb8678d1b849f3` (same production tree).
- Exact tests: R4, `test_alternating_failure_classes_eventually_stop[1]` and `[2]`.
- Boundary: real `engine._handle_failure` and SQLite checkpoint updates; `recovery.Ladder.from_dict` discards spent state when the failure class changes.
- Input: create task through API with `max_retries=0`, claim its run, invoke the production failure handler 12 times alternating `unsupported tool use` and `empty response no content`.
- Expected: spent degraded route stays spent across changing provider errors; recovery terminates after finite alternatives are exhausted.
- Actual: all 12 transitions remain `queued`; attempt increases to 12; checkpoint alternates capability/silent with `spent=['degraded_path']` on every transition.
- Reproducibility: 2/2 separate service/database instances, 24 observed transitions. Repository-fixable: YES.
- Severity limit: provider calls and full worker execution were not exercised by this reproducer. No infinite mission, token bill, or bypass of all other engine guards claimed; classify P2, not P1.
- Narrow remediation direction: retain a cross-class recovery-attempt ceiling or preserve spent path identities across error-class changes.

### Additional completed negative control

R4 `test_apps_repeated_enable_start_stop_disable`: PASS, 1/1 test containing two full cycles of enable twice, start twice, stop twice, disable twice, then refused start.
Uses real disposable child processes and HTTP API. Duplicate starts report already-running; disabled control refuses another start. Fixtures terminate their processes.

## Auditor's conclusions and decision record

- Freeze remains BLOCKED by the twice-reproduced protected-file evidence bypass F1. Six further P2 defects remain unfixed; no P0 demonstrated.
- Strongest evidence: F1 executes an actual sidecar and inspects protected bytes afterward; F5 crosses the actual HTTP route; F6 persists every transition in SQLite. These are not source-only suspicions.
- Rejected inference: a green parser suite proves complete responses. R3 is green while F3 returns `ok=True` for a malformed partial response; the suite and attack have different assertions.
- Rejected inference: pure memory-generator tests prove the HTTP boundary. F5 demonstrates the route changes the input before validation.
- Rejected inference: F6 alone proves a runaway worker or monetary loss. Full worker execution remains unverified, so that stronger claim is deliberately omitted.
- Rejected inference: OpenHands scope validation is OS containment. Existing source explicitly disclaims that. F1 instead breaks the evidence boundary the client actually promises.
- First fix priority: F1, independent evidence admission. Next: F6 recovery boundedness, F2 consumption cap, F5 route validation, F3 incomplete-response status, F4 Windows cleanup. These are proposals only; no fixes applied.
- Next verification ideas: rerun F1 with a hostile index plus an ordinary allowed-file control; drive F6 with alternating real adapter failures through claim/execute; test F2 with a paced loopback stream; rerun cleanup twice on Windows and verify residual bytes. None is counted as completed here.
- Coverage limit remains material: selected suites were interrupted twice; Video/Web post-state, full browser/desktop runtime and live providers were not certified. This is a narrow breaker audit, not a whole-repository or release-completeness verdict.
- Decision summaries above are the reviewable rationale requested by the owner. Raw commands and observed results are in the supplied tests/logs; no speculative findings were added to fill the report.

## Owner-machine UI continuation and final checks

Browser control connected to the owner's existing Edge tab at 18:58 UTC; the earlier access blocker was resolved. UI actions finished around 19:04 UTC.
Live process PID 36892 was launched from the original workspace, not the audited worktree. Disk HEAD was `1531b1609f4aa5c7f993edef43fc59423b724472`; loaded-module SHA is UNKNOWN. Live observations are not asserted to run night HEAD.
Hardware observed: Windows 11, 15.6 GiB RAM, RTX 4060 Laptop GPU; UI showed approximately 14.1/15.6 GiB used.
GLM 5.3 probe through Models returned success, latency 9833 ms. Existing configured credentials were used; the key supplied in chat was not used or recorded.
Evidence: `A/runtime-ui.log`; only audit-created tasks/project were manipulated. No production code changed.
Skills: webapp-testing and computer-use guided visible UI checks; owner explicitly requested the existing session instead of an isolated UI.

### F7 — P2: negated tool request is classified as required terminal action

- Exact tested code SHA: `45027d3e9aef554407a0a9ff07fb8678d1b849f3`, with audit-only commits through `561756242cde4eab3ce6e529083e91a190b47175`.
- Exact command, with PYTHONPATH from R1: `python -c "from bcc.features.action_contract import classify_all; [print(i+1,[c.name for c in classify_all('Calculate 17*23. Do not use tools or write any file.')],[c.name for c in classify_all('Calculate 17*23.')]) for i in range(2)]"`
- Expected: both arithmetic-only requests produce no required action contract.
- Actual: each negative-instruction request returns `['TERMINAL_FILE_ACTION']`; each positive control returns `[]`.
- Reproducibility: 2/2 on audited tree. Repository-fixable: YES. Baseline regression not established.
- Boundary: `command-center/bcc/features/action_contract.py`, terminal-action regex/classify_all; negation is ignored.
- Additional live observation, separately SHA-unattested: A1 arithmetic JSON request with Russian prohibition on tools/files failed run #33 with `action_contract/no_verified_action`, after two GLM responses (107 and 300 tokens). Review demanded terminal.kill/run/stdin.
- Positive UI control A2 completed run #34 after one 65-token model step. Its answer content was not displayed in the inspected task log; mathematical correctness is NOT certified.
- No unauthorized tool effect demonstrated. Severity remains P2, not authority-bypass P1.

### Unattested UI observation: Web Designer repeated Apply reverts text

- Not included in SHA-qualified findings/counts: running application source SHA is unknown.
- Exact steps: create audit project `ASTRA_UI_WEB_20260908`; save HTML heading `ASTRA_WEB_SAVED_20260908`; reload; click heading; set inspector text to `ASTRA_WEB_EDITED_20260908`; Apply; Apply again without editing.
- Expected: second Apply retains EDITED, since inspector should reflect the applied value.
- Actual: first Apply updates editor/iframe to EDITED but inspector resets to SAVED; second Apply reverts editor/iframe to SAVED. One complete observed sequence; source attribution unverified.
- Save/reload before this attack preserved the code and iframe heading (PASS). No general Web Designer success claim; project 2 retained for owner inspection.

### Completed selected checks, same audited production SHA

Command R5: `python -m pytest -c command-center/pyproject.toml -o addopts='' -p no:cacheprovider --timeout=20 --basetemp=artifacts/astra_codex_breaker_20260908/agent-check-tmp command-center/tests/test_authorization_at_effect_time.py command-center/tests/test_video_native_finalize_contract.py command-center/tests/test_trading_lab_unwired.py -q --tb=short`
Result: 41 passed, 1 warning in 32.37 seconds; `A/agent-check.log`. Warning: aiosqlite thread raised Event loop closed; not promoted to a finding from one occurrence.
These completed checks supplement, but do not convert, earlier interrupted suites to PASS.
Final verdict: P0=0, P1=1, six further P2 findings; freeze BLOCKED. Live UI observation above is additional, explicitly source-unattested evidence.
