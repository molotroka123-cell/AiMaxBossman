# Final code completion — 2026-09-09

One line of code, one frozen SHA, one set of numbers. Every number below was
measured on the tree it is attributed to and is never carried over from an
ancestor.

## 1. Remote truth, verified before anything was changed

Each SHA was resolved locally with `git cat-file` / `git rev-parse`, not taken
from the task description.

| | SHA | Note |
|---|---|---|
| Shared base (PR #58 head) | `45027d3e9aef554407a0a9ff07fb8678d1b849f3` | branch `night/v7-convergence-20260908` |
| PR #61 runtime freeze | `910ca901ddcd84c13d65f9246dfec394088c29bf` | 16 commits above the base |
| PR #61 head | `5c19eea58691599cf06764b020a4ea8ca51c0285` | one commit above `910ca90`, touching one documentation file |
| PR #60 unified experiment | `92e89e0140cd8682c2a0d9c9fcf35f0435506715` | |
| PR #60 head | `df8fea50275bd80696b1690a924c8a8e8b48872f` | |
| Astra breaker | `d33288f78584a406b124611066697e735d690f9d` | `audit/astra-v6-latest-breaker-20260908` |
| GitHub default | `799fc3dd8e4327811be9d8f3e33cc43ce8168977` | `main`, not merged |

**BASE_SHA = `5c19eea`** (runtime identical to `910ca90`; the extra commit is
`docs/final_closure/FINAL_CLOSURE_REPORT.md` and nothing else — verified by
`git diff --stat`).

### Why PR60 was ported and not merged

```
git diff --stat 5c19eea..df8fea50
205 files changed, 6348 insertions(+), 16965 deletions(-)
```

The deletions are PR61 work absent from PR60. Merging PR60 would have removed
roughly eleven thousand net lines of later closure work to gain File
Intelligence. So the File Intelligence semantics were carried onto the newer
base instead.

## 2. What was ported

| Area | Files |
|---|---|
| Runtime | `command-center/bcc/file_intelligence/` — `models`, `scope`, `verify`, `runtime_lock`, `privacy`, `discovery`, `protocol`, `service` |
| Capability | `command-center/bcc/features/file_intelligence.py` — typed argv, module-level router |
| UI | `command-center/ui/pages/file_intelligence.js`, styles, one nav entry |
| Integration | `integrations/ai-file-sorter/` — manifest, NOTICE, README |
| Tests | four suites plus `command-center/tests/fileintel_fake.py` |

Both later File Intelligence hardening commits are included and were confirmed
present in the ported code rather than assumed: effect-time binary identity
re-check (`_assert_binary_unchanged`, from `9bea81a`) and single route
declaration with a `ROUTE_COUNT` assertion (from `a1e633c`).

### The contract is unchanged

External sidecar, no vendored AGPL source, off by default, explicit activation,
typed argv with no shell channel, review-only headless operation, automatic
destructive apply impossible, binary identity pinned and re-checked at effect
time, local-by-default with remote failing closed, workspace containment with
`.git` and Bossman state protected, symlink escape refused, stale plans and
source-hash mismatches refused, post-state verified, crash reconciled, runtime
locked, and repeated application startup adding no routes.

## 3. What was deliberately NOT taken from PR60

Each was compared rather than assumed. In every case the base was stricter, so
the base won.

| Area | Comparison | Decision |
|---|---|---|
| Trader | base keeps `_ordered_history()` and isinstance-checked `SeriesId`; PR60's copy had both removed and used truthiness | keep base |
| `run_provenance` | base carries 303 lines PR60 lacks, including `test_run_provenance_postgres.py` | keep base |
| STOP_GRACE | base asserts full row equality **and** `claim() is None` after restart; PR60 asserted row count and absence of `running` | keep base |
| AP-001 | identical test names; base proves containment with a failed `cat` and an absent token rather than an `error` flag | keep base |

## 4. The one defect the integration surfaced

**Reproduced first.** In a clean venv, with the checkout not on `sys.path`:

```
MANIFEST_PATH = .../venv/lib/python3.11/integrations/ai-file-sorter/integration.json
exists        = False
pinned_sha    = ''
```

`_MANIFEST_PATH` resolved to `parents[3]/integrations/...` — the repository root
in a checkout, the **interpreter directory** in site-packages. The read failed
softly to `{}`, so an installed Bossman answered `/file-intelligence/status`
with an empty `pinned_upstream_sha` and wrote an empty `upstream_sha` into every
review envelope. The declared identity of the sidecar integration existed only
in the build nobody runs.

**Fixed** the way this repository already ships the UI: `command-center/setup.py`
copies the manifest and NOTICE into the wheel as `bcc/_integrations/ai-file-sorter/`,
`discovery.py` reads the packaged copy first and the checkout second, and a
missing manifest raises at build time rather than degrading at the owner's.

**Verified after:**

```
manifest_path = .../site-packages/bcc/_integrations/ai-file-sorter/integration.json
pinned_sha    = '4dc374df69b5e63d5354e121097d92e25bbd32da'
```

Regressions added: `tests/test_packaging_installed.py::test_command_center_wheel_carries_the_file_intelligence_pin`
(the built wheel carries the manifest, byte-identical to the checkout, with a
non-empty pin), a resolution-order test with its negative control (packaged wins,
checkout is the fallback, neither present returns `None` rather than a false
match), and an assertion inside `tools/verify_installed_product.py` so every
installed-product run re-proves it.

## 5. Evidence

### Gate A — focused integration, local, on the frozen tree

| Suite | Result |
|---|---|
| File Intelligence (4 suites) | **130 passed, 1 skipped** |
| OpenHands hostile evidence (8 modules) | 103 passed, 10 skipped |
| Gateway contract (16 modules) | 169 passed |
| provenance + PostgreSQL terminal + completion truth + negation routing | 115 passed, 6 skipped |
| Apps lifecycle + STOP_GRACE | 103 passed, 1 skipped |
| Web Designer (12 modules, real Chromium) | 117 passed, 0 skipped |
| Video Studio (24 modules, real FFmpeg 6.1.1) | 343 passed, 11 skipped |

The single File Intelligence skip is NTFS junction/reparse semantics, which need
Windows and are an owner-machine boundary. It is registered in
`docs/testing/SKIPS_REGISTRY.md` with a reason, like the other 192.

### Gate B — packages

Local:

| Suite | Result |
|---|---|
| root, py3.11 | 1257 passed, 2 skipped |
| root, py3.12 | 1249 passed, 10 skipped |
| Command Center UI (node) | 42 passed, 0 failed |
| `node --check` on every UI file | clean |

The full Command Center suite in this sandbox: **3214 passed, 23 skipped, 2 failed
in 23:59**. Both failures were reproduced, traced to a mechanism, and each has a
passing control. Neither is caused by this integration and neither is a product
defect — see §5a. The authoritative Command Center numbers are the exact-SHA CI
jobs, which run without this sandbox's two peculiarities.

Regression targets named in the task, run individually:

| Target | Result |
|---|---|
| AT-01 / AT-03 (6 modules) | 143 passed, 1 skipped |
| AP-001 containment | 10 passed, 2 skipped |
| Video CFR / container correctness | 44 passed |
| N4 fairness | 33 passed |
| N5 promotion (incl. durable ledger) | 80 passed |
| N6 objective workspace | 15 backend + 7 node passed |
| N8 canary/rollback (incl. production caller) | 70 passed |
| DB refusal does not leak the DSN | 3 passed |
| installed packaging | 2 passed |
| owner breaker harness | 9 passed |

PR26 and PR36 have no reproducer in this repository. PR36 is the historical PR
that carried AT-01/AT-03, which pass above. PR26 is the retired closure
checkpoint whose carried-forward areas were restart/M1, CFR and host
environment — covered by the rows above. Per the rule that no reproducer means
no production change, neither was reopened.

### 5a. The two sandbox failures, classified rather than waved away

Both were re-run in isolation, then re-run on the **untouched base `5c19eea`** in
the same sandbox, where both fail identically. So neither is caused by the File
Intelligence port. PR #61's own Command Center CI on `5c19eea` reports
**3078 passed, 23 skipped, 0 failed** — so neither reproduces on a runner either.
Each mechanism was then proven with a control.

**`test_browser_installed_readiness.py::test_repeated_probes_do_not_create_drivers_or_sessions`**

The product resolves `PREINSTALLED_CHROMIUM = "/opt/pw-browsers/chromium"` before
anything else. This container ships a real Chromium symlink at exactly that path;
a GitHub runner does not. So the product correctly prefers the real preinstalled
browser over the fixture's fake binary, and the test's equality assertion fails.

> Control: with `/opt/pw-browsers/chromium` temporarily moved aside, the module
> is **30 passed, 0 failed**.

**`test_v21_e2e_mission.py::test_autonomous_mission_with_ten_plus_tool_calls`**

Two stacked container differences, and the second one is the product being right.

1. `browser.open` was refused: *"адрес 127.0.0.1 непубличный … запрещён без
   `BCC_BROWSER_ALLOW_PRIVATE=1`"*. The SSRF/loopback guard doing its job; the
   variable simply was not set in this shell.
2. With it set, all ten tool calls execute — and the task **still** ends `failed`,
   with the run carrying `effectful terminal outcome is failed or still unobserved`.
   The scripted mission runs `python -m pytest -q` inside the project. In this
   container bare `python` is `/usr/local/bin/python`, which has no pytest, so the
   "test was red, I fixed it, the rerun is green" step genuinely did not happen —
   and the completion-truth gate refused to call the task completed even though the
   model's final answer confidently claimed success.

That second point is the MF-001 P0 contract working exactly as specified: the
model said it happened, the effect was not observed, and the product did not
report DONE. Editing production code to turn this green would be weakening a
contract to buy a green tick, which this run does not do.

> Control: with a runner-like `PATH` (an interpreter that has pytest) and
> `BCC_BROWSER_ALLOW_PRIVATE=1`, the module is **2 passed, 0 failed**.

### Gate C — the installed product

Linux, from a clean tree, using the shipped installer:

| Step | Result |
|---|---|
| `tools/build_local_bundle.py` | 14 artifacts, source `e5ba10e`, not dirty |
| `dist/bossman-local/install.py` into a clean venv | PASS |
| installed boot / auth / UI assets / health / restart / persistence | PASS (59 assets served) |
| File Intelligence in the installed product | `enabled=false`, `pinned_upstream_sha=4dc374df…`, `binary=NOT_INSTALLED` |
| installed UI, real media, recovery, fresh-process restart | 13 passed, 0 skipped, 0 failed |
| source checkout after the installed run | unchanged (`git status` empty) |

Windows, on the hosted runner: `local bundle (windows-latest)`,
`windows paths (py3.12)`, `Windows workspace and PID contracts`,
`ASTRA portable (windows-latest)` and `File Commander (windows-latest)` all
green. That is **build/install/paths/startup/cleanup only**. Browser and UI
behaviour on real Windows is **OWNER_WINDOWS_REQUIRED** and is not claimed here.

## 6. What is honestly not closed

| Item | State | Why |
|---|---|---|
| Intelligence retention | `INSUFFICIENT_EVIDENCE` | the gate refuses to pass without `docs/benchmark/intelligence-preservation-current.json`, and that file may only come from a genuine same-model baseline/current comparison. No model credentials exist in this environment. Nothing was fabricated, no threshold was lowered, and no substitute model was used. The red job is the honest answer, not a regression. |
| Windows owner UI | `OWNER_WINDOWS_REQUIRED` | hosted Windows proves build, install, paths, startup and cleanup; it does not drive a browser |
| Local model | `OWNER_LOCAL_MODEL_REQUIRED` | Ryzen AI Max+ 395 / Radeon 8060S / 128 GB is not a hosted runner |
| Real OpenHands, OpenRouter/GLM | `OWNER_LIVE_REQUIRED` | no credentials in this environment |
| File Intelligence with the real AIFS binary | `OWNER_LIVE_REQUIRED` | the hosted suites run against a deterministic fake sidecar and say so |
| Canary / rollback in production | `OWNER_CANARY_ROLLBACK_REQUIRED` | the repository rehearsal passes (N8, 70 tests); real deployment infrastructure is a separate thing |
| Soak | `OWNER_LIVE_REQUIRED` | needs hours on the owner's machine |

None of these is a repository-fixable blocker. Each is a missing piece of the
owner's world, named rather than papered over.

## 7. Owner acceptance

`python scripts/owner_breaker.py plan` prints, for every item, what a named
hosted suite already proved and what only the owner's machine can answer.
`HOSTED_PASS` and `OWNER_LIVE_PASS` are separate verdicts; an item with an
owner-live half stays in `owner_live_outstanding` no matter how green CI is.
B1–B10 as specified, plus ten live extensions.

The existing twelve-scenario `scripts/evening_acceptance.py` is untouched.
