# Windows process queries and root-CI latency triage

Runtime tested locally: `6332648246c8067844f9649939de7c53dba69d2e`.
Base reviewed: `3d165596ada9da3ed742d5da1c4b374a336c1080`.
CI input: `83c1a02d6c8066ff267d3746b82bd31171d268b5`.
This is a bounded diagnostic supplement, not a release acceptance verdict.

| Finding | Severity | Reproduction and cause | Fix | Controls and status |
|---|---|---|---|---|
| AF-WIN-001 | P1 | Apps recovery queried `os.kill(pid, 0)`; the browser profile lock did the same. On Windows, zero is a console event, not a POSIX existence query. Windows CC job 102280222348 ended in KeyboardInterrupt after 149 tests, immediately before the first Apps recovery test. | `7414134e65b39bbb991708d4669a1e8463ee16c9`: Apps uses the existing psutil dependency; Core uses a typed Win32 SYNCHRONIZE handle and zero-time wait. PID/create-time/executable/argv ownership checks remain. Unknown browser process state keeps the lock. | Restored old helpers both fail the new no-signal negative controls. Live/absent/unknown/access-denied and 64-bit-handle cases pass. Native Windows rerun is required; simulated Win32 calls are not live acceptance. |
| PERF-SCAN-001 | P2 | Each `ast.Call` caused `ast.get_source_segment` to rescan its entire source before checking the function name. CI spent 60.44s and 60.01s in the two registry tests. | `6332648246c8067844f9649939de7c53dba69d2e`: filter irrelevant names before extracting source. | Local scan 9.509s -> 1.290s; both render exactly 163 rows with SHA256 `28c7070c9a6fb4f2bf25e8f4bd688e81464d7f0f203a432138eeeb8c3709cd3a`. A 2,000-call negative control rejects the old scanner. No skip classification changed. |
| PERF-OP-001 | P2 | Pretty-printing the complete operator restart journal at every state transition invoked Python's recursive JSON formatter on all prior steps. Profiling showed 78ms in JSON encoding and 114ms in 64 save calls. | `6332648246c8067844f9649939de7c53dba69d2e`: compact JSON; same values, same fresh reads, same writes, same CAS and errors. | Instrumented equivalent arm: JSON encoding 4ms; 64 saves 40ms; overhead 141.35 -> 68.19ms. Owner, effect, completion, recovery and sensitive-summary controls pass. Instrumented values are diagnostic, not latency acceptance. |

Windows behavior is documented by [Python's os.kill contract](https://docs.python.org/3.12/library/os.html#os.kill) and [Microsoft's console-event constants](https://learn.microsoft.com/en-us/windows/console/generateconsolectrlevent). The historical CI interrupt's attribution remains an inference until the repaired native job completes.

## Acceptance evidence and limits

On runtime `6332648246c8067844f9649939de7c53dba69d2e`, Python 3.12.13:

- 117 focused tests passed. This includes Windows API portability controls, browser policy, operator owner/completion/effect/recovery, deterministic SQLite connection closure, all timing-contract negative controls, scanner cost control and the observation-reuse saving test.
- No threshold, timing assertion, SQL durability mode, per-operation SQLite closure, provider cost or effect-boundary probe changed.
- Native Windows is not available on the local execution host. Add `command-center/tests/test_apps_pid_portability.py` to the existing native Windows gate and rerun the real Apps restart/stop tests. Core's new `test_browser_pid_portability.py` must also run there.
- Python 3.11 is not available locally. Its CI result is still required on the final release SHA.

Local evidence files are retained in `/workspace/scratch/edc419faf64b/`:

- `performance-windows-6332648.xml`: 117 focused tests.
- `performance-3d16559-focused.xml`: initial unmodified two-test run.
- `registry-baseline-3d16559.json`, `registry-filtered-3d16559.json`: complete-scan timings and identical output hashes.
- `performance-diagnostic-3d16559.json`, `performance-diagnostic-compact.json`: instrumented SQL and operator profiles.
- `root-resource-6332648.json`, `root-resource-6332648.log`: one full-root diagnostic run, including per-test CPU/wall time, GC and thread census.

## Original latency failures: retained as failures

CI Python 3.11 CAS failed with p50 1.482ms, p95 4.221ms and three over-limit writes: 94.796ms, 25.605ms and 19.607ms. Its floor p50 was 0.422ms, floor body 1.890ms and floor maximum 3.122ms. The unchanged contract correctly returned FAIL. The reuse comparison also failed: 936.527ms with reuse versus 827.764ms without it.

The unmodified local CAS reproduced a different FAIL: every operation was below 2.51ms, but p50 0.318ms / floor 0.0108ms = 29.45, exceeding the unchanged 8x ratio. Instrumentation found exactly 100 connections opened, committed and closed; cumulative costs were 4.49ms open, 18.13ms WAL pragma, 3.06ms transaction exit and 6.72ms close. Retaining connections would violate the existing explicit-close contract, so no pool or GC-dependent lifetime was introduced.

The full-root diagnostic run on the fixed runtime recorded 1201 passed, 6 failed, 2 skipped in 60.26s. It is not a green acceptance run:

- Reuse saving passed: 1378ms total for both arms, 92.6ms CPU, 0.25ms GC, one thread before and after.
- CAS failed the local floor ratio again (p50 0.479ms, max 1.317ms, floor p50 0.011ms), with only 0.35ms GC. The operator framework/floor gate and the injected-stall positive control also failed their unchanged relative contract on this host.
- Gen-2 GC elsewhere reached 95.85ms, but no such collection occurred within the two measured runs. This does not establish GC as the cause of the CI stalls.
- One watchdog output-reader thread survived the inherited-pipe timeout test; the maximum thread count was two. The host exposes virtual process IDs beside a different `/proc` namespace, preventing trustworthy recovery of an unobserved grandchild. This must not be presented as a passing process-tree cleanup proof.
- Other failures: the pre-existing stale skip registry, the v6 host-PID probe and a clean-venv import of `bossman_shared` after pip reported success. The packaging owner must reconcile the latter against the actual bundle acceptance; this report does not excuse it as runner noise.

The stale registry difference already existed at the base: terminal containment tests moved one skip from line 159 to 171 and added another at 269 (162 -> 163 entries). Regenerate from the final converged tree; do not remove those real requirements to match old documentation.

The exact final CI run must resolve the remaining timing gates. These fixes and measurements are not permission to relabel any red result as PASS.

## CAS comparator correction, with an additional CPU contract

The subsequent investigation proved the old CAS comparator wrong in both directions. Its retained SQLite connection omits costs that the store's explicit-close contract requires on every operation. A balanced, interleaved 100-sample experiment on `73a8bf8efd8524a4da88ba6465980ac81c610933` measured ordinary CAS p50 0.327717ms, retained floor 0.014081ms and a minimal one-table fresh-connection write 0.208941ms. Even that minimal write, before any ObjectiveStore schema, serialization or CAS logic, exceeded the old floor by 14.84x and failed its 8x contract. The real CAS/matched-floor ratio was 1.568x. Raw controls remain in `objective-cas-matched-73a8bf8.json`; the old failures above are not converted to PASS.

The reverse defect also occurred in actual Windows CI: Human-speed run [34291958255](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34291958255), job 102280222156, artifact 10081733667. The real 4ms burden had wall p50 17.6017ms and floor p50 2.612ms (ratio 6.738x, all 100 writes over 10ms), yet the old wall-only contract returned PASS. The regression test correctly rejected that gate verdict. Its extra assumption that the burdened wall median would stay below 10ms is also disproved by these measurements; replacing that premise is a test correction, not evidence that the real CPU regression became acceptable.

The corrected CAS measurement now requires both wall time and current-thread CPU time to pass the original `latency_contract`. Each keeps the same 8x ratio, 10ms absolute bound, complete 100-sample population, raw maximum, body/outlier limits and one-isolated-stall allowance. Neither clock substitutes for the other. The floor performs only one integer UPDATE, with WAL/FULL durability, a fresh connection and immediate commit/close. Floor-before-CAS and CAS-before-floor alternate 50/50; all four raw sample arrays and measurement order are retained. A 4ms burden consumes actual thread CPU so scheduler pauses cannot consume its negative-control workload. Slow storage can no longer buy a CPU-regression allowance.

`ObjectiveStore`, its SQL, durability and connection lifetime remain unchanged. Removing its per-operation WAL pragma improved the local median only about 2%; an UPDATE/RETURNING prototype was slower. Neither unsupported optimization was retained. The existing retained `StorageFloor` and operator timing contract remain unchanged; this correction applies only to CAS.

The new floor test holds strong references to every connection and proves each handle is already closed, committed data survives reopening and FULL/WAL is used. The collector preserves version increments, stale-write refusal, restart equality and DRAFT/UNKNOWN status. The actual 4ms and 12ms CPU regressions must fail; synthetic Windows wall data separately proves that a slow disk cannot hide the CPU failure. The first corrected focused run was 171 passed in 3.42s on Python 3.12.13; it is local component evidence, not a final-SHA CI claim.

To reproduce on a clean checkout of the exact candidate (the JSON retains SHA, timestamp, Python, SQLite, fixture spec, raw samples and both verdicts):

```bash
python tools/objective_cas_profile.py --expect-sha <40-character-SHA> --json-out /absolute/new/cas-evidence.json
python -m pytest tests/test_v5_human_speed.py tests/test_cas_latency_profile.py tests/test_human_speed_gate.py tests/test_v5_connection_lifetime.py tests/test_v5_objective_store.py -q
```

The command refuses a dirty or mismatched checkout and never overwrites evidence. Its PASS means the baseline passed both contracts and the real CPU regression failed; it does not certify the release. Native Windows, Python 3.11 and the full root workflow still require execution on the final convergence SHA.
