# Convergence input ledger — Astra closure 2026-09-10

This ledger records the fetched input snapshot, not a release verdict. The owner asked to
continue **PR #61**, so its existing branch remains canonical. No source ref is deleted
or force-pushed. Final acceptance must bind to the new final commit in that branch.

Recovery floor: `45027d3e9aef554407a0a9ff07fb8678d1b849f3`.
Canonical input: `c7e75cc3c0a636d61f6fc8fb3e476a210d1320d3`.
Snapshot timestamp: `2026-09-08T22:59:15.897515+00:00`.

## Graph and intended integration order

| Input | Exact head | Merge base with recovery floor | Unique commits | Disposition |
|---|---|---|---:|---|
| default: `claude/bossman-control-v03-43igbk` | `a6762965c5806d5c44462ee1a516dabf6307fe81` | `1bb39bf8dc656cf21a9bd03f85f6751d87d725c7` | 29 | Do not merge wholesale; retain stronger existing ports; exclude new Trader work. |
| main: `main` | `799fc3dd8e4327811be9d8f3e33cc43ce8168977` | `799fc3dd8e4327811be9d8f3e33cc43ce8168977` | 0 | Already ancestor of recovery floor; provider/main gateway composition retained. |
| PR #58: `night/v7-convergence-20260908` | `45027d3e9aef554407a0a9ff07fb8678d1b849f3` | `45027d3e9aef554407a0a9ff07fb8678d1b849f3` | 0 | Recovery foundation already ancestor of PR61. |
| PR #61: `claude/bossman-final-audit-closure-aucx9x` | `c7e75cc3c0a636d61f6fc8fb3e476a210d1320d3` | `45027d3e9aef554407a0a9ff07fb8678d1b849f3` | 6 | Canonical line; retain all completion/evidence fixes and build forward. |
| PR #60: `claude/bossman-final-integration-pass-04nffa` | `e5ccd54c8fea94940f2ebced41bcb583076ac264` | `45027d3e9aef554407a0a9ff07fb8678d1b849f3` | 12 | Select provenance/identity/lifecycle corrections; optional File Intelligence admission deferred until core boot. |
| PR #54: `build/local-bundle-20260907` | `210cc525fd0d571facffaebf60a4456eb2cf267b` | `7b67b95c6f154298063c63cfe611d8ff91e35a14` | 1 | Required packaging intent; strengthen builder and installed UI proof. |
| PR #59: `feature/ai-streamer-higgsfield-browser-20260908` | `128324713c140c908a7ad502ed5621e5ea22a6a6` | `45027d3e9aef554407a0a9ff07fb8678d1b849f3` | 42 | Optional subsystem deferred under emergency override; retain source ref as evidence. |

The complete pairwise merge-base matrix and changed paths are in the adjacent JSON.
There are 90 unique input commits relative to the floor (including ancestry merges).
Classification is a release-scope decision, not proof that a deferred subsystem is safe.
`EXPERIMENTAL_NOT_FOR_RELEASE` also marks unrelated changes deliberately deferred by
the emergency order; it does not allege a defect in those changes. Optional admission
requires a later explicit ledger update and its full companion fixes/tests.

## Semantic overlaps and retained contracts

* Gateway conflict is historically resolved by `895ef5ba0fea99227ea609b52a2e202c2ff7eb87`,
  already an ancestor of floor and PR61. It kept auth, budget, approvals, router,
  circuit breaker, semaphore, timeout, redaction, CLI and nine provider presets.
  Main `799fc3d` is an ancestor, so re-merging it adds nothing.
* Gateway composition tests prove presets/text/catalog plus a Groq breaker control,
  but not complete Anthropic compatibility. A fresh actual converter probe on
  PR61 reproduced dropped outbound and inbound tool calls. Its native SSE is
  also forwarded raw into an OpenAI stream contract (inspection). This is a new
  repository defect, not a reason to restore the unauthenticated main app.
* Default Trader through `da67a63` was already semantically ported in `d415db4`,
  with Decimal entry-price behavior and stronger tests. PR60 `355a582` closes
  metric identity in this existing module. New buyer-failure regimes are excluded.
* PR60 provenance changes `api.py`, `db.py`, `engine.py`; integrate around PR61
  finalization and authorization rather than copying an older engine. Its code-SHA
  lookup and capture-failure behavior still need installed-runtime review.
* PR59 adds 8,332 lines across 46 paths, principally a separate Social Farm
  subsystem. It has no required dependency for Command Center boot or editor
  playback. Its `features/reality.py` overlap must preserve PR61 nonfinite/invalid
  requirement refusal if optional integration is attempted.
* File Intelligence is not equivalent to File Commander. Deferring its sidecar
  must not remove the owner's core files/apps workflow. If admitted, never omit
  `9bea81a` binary identity or `a1e633c` repeated-router fix. Its accepted fixture
  evidence does not prove the real AI File Sorter binary/model.

## Packaging findings passed to the packaging workstream

PR54 only sets `BCC_UI_DIR` and puts UI beside wheels. Its wheel still has no UI
by itself. Its verifier inherits source cwd, imports packages and checks entrypoint
files/UI paths; it does not boot BCC or prove its worker. Its manifest hashes wheels
only while the readme claims every file. `--out` is recursively removed without a
containment check. These are source findings requiring regression/clean-install
proof, not closed findings. No final artifact or package PASS is claimed here.

## Historical evidence reconciled

All mandatory references were read from their owning input revisions:
`MASTER_FINDINGS`, `FINAL_CLOSURE_REPORT`, `OWNER_SESSION_CLUSTERING`,
`OWNER_BREAKER_SETUP` at PR61; `OWNER_TEST_20260908_FILE_INTELLIGENCE` at PR60;
`NIGHT_V7_CONVERGENCE_FINAL_REPORT`, `V7_MULTIMODEL_AUDIT_REFERENCES`,
`MULTIMODEL_AUDIT_SYNTHESIS` at floor/PR61; `IMPLEMENTATION_MAP_20260908` at PR59.

| Historical source | Anchor | Current interpretation |
|---|---|---|
| Astra breaker | `d33288f7` | Tested floor; independent evidence bypass, stream cap/truth, recovery/NaN/negation seeds were addressed by PR61. Must regress current tree. |
| Owner manual audit | `1531b1609f4aa5c7f993edef43fc59423b724472` | Empty completion, CAPTCHA completion, model picker and missing Coding path confirmed; live loaded SHA unknown. |
| Independent total audit | `1da5b577` | Default and candidate differed; authorization/DENY/benchmark fixes later landed in floor. Historic scores are not acceptance. |
| Owner journal | `5893ffba6724` around `966c16c3` | 13,728 historical records; loaded runtime SHA not recorded. |
| Owner journal superset | `2fdf5629d0d9` around `2903b5b` | 14,614 records; 13 agentless blocked tasks; 114 network-unreachable refusals; obsolete Video commands 404 and executor ValueError still require owner-path reproduction. |

The raw journal filenames at `2903b5b` include the named 18:41:19 and 18:52:14
exports. Their session clustering is historical evidence, never proof of current
installed acceptance. UI replay must record actual serving SHA in this run.

Stale claims requiring final-report correction:

* Night report calls `72bae3e` FINAL and the gateway conflict external; later
  `895ef5b` actually resolved it. Its `/api/health` claim does not create a BCC
  health route: PR61 exposes its health payload through `/api/system`.
* MASTER_FINDINGS calls post-`69df482` commits documentation only; `4001b7a`,
  `cbb2991`, and `c7e75cc` contain code or test corrections.
* PR61 FINAL_CLOSURE_REPORT binds results to several historical SHAs and leaves
  jobs in progress. They must not become one final-SHA PASS.
* PR60 freeze points at `a1e633c` and explicitly excludes PR59. It is not a
  converged release claim. Intelligence Preservation remains insufficient.

## Every unique input commit

The table classifies every commit before any optional merge. REQUIRED means
retain or selectively integrate and verify; it does not mean the parent release
engineer has already integrated it. Evidence and current disposition belong in
the final exact-SHA closure report.

### default — `claude/bossman-control-v03-43igbk`

| Commit | Classification | Decision |
|---|---|---|
| `d05318bbb1592638a7b0271d054a0cd1ec8ba995` | DUPLICATE | Already preserved in floor by patch-equivalent b41232b492913e05497c1e3596e83cb49be058ff (setup-python v7); git cherry reports equivalent patch. Do not apply twice. |
| `ac245c738c261bd616b3d2ef1ebfac670c1e31eb` | DUPLICATE | Already preserved in floor by patch-equivalent e2d8028fd24c996c75b22ff30aa37f43f93fd7d9 (create-pull-request v8); git cherry reports equivalent patch. Do not apply twice. |
| `fca0eafd9b8d2366b7d040af8a57c3fefeeb03b5` | DUPLICATE | Already preserved in floor by patch-equivalent e7a6d908795650ec6d537f9a3ef1a185f255f538 (upload-artifact v7); git cherry reports equivalent patch. Do not apply twice. |
| `29b26563b56582a7dc0ad8fb9aba99f6eabbd479` | DOC_ONLY | Already preserved in floor by patch-equivalent 6595c006d0acbf56913263c8b9a0b6b98cff111e (historical PR triage); git cherry reports equivalent patch. Do not apply twice. |
| `8567ffac27b7c4b2929b850d9a0d181330dbbcd7` | DOC_ONLY | Already preserved in floor by patch-equivalent 2e6cd3f648e5ccedde901967dadbcb4e17bfc44c (historical audit backlog); git cherry reports equivalent patch. Do not apply twice. |
| `7638558a9251d0a28868504fafb33b520eca4258` | DOC_ONLY | Local model candidate matrix; no measured local model acceptance. |
| `b57b4ce981458581600adc2ee6836abca58be6fb` | DOC_ONLY | Local model orchestration training specification, not release implementation. |
| `d5187cdf94510b5db9ab02f56cc14015af240844` | DOC_ONLY | Historical hardware audit; not evidence of available owner hardware. |
| `ddea21112f89c978df50aae8948c5955d7dada2e` | DOC_ONLY | Historical performance/intelligence audit; no new runtime implementation. |
| `8abb7a6ecdc6abe6f51ef068508d5d76fecfe23f` | SUPERSEDED | Trader implementation ported by d415db401ad75f81536340f39137b2c4ef1a1e52 with stronger Decimal contract. |
| `48816bc54bab89343fcf0a78ea8e89dd6aafccf0` | SUPERSEDED | Original Trader tests preserved and strengthened by d415db4; do not replace the stronger suite. |
| `10469c524760a711023cfa9de09d7875a997fcb9` | DUPLICATE | Trader playbook already ported by d415db4. |
| `72da311865440839f0d8be0b373567ba0b635eea` | DUPLICATE | Trading rule corpus already ported by d415db4. |
| `55ad9c7993808e3b4c3ef638e021a26a4df497c4` | DUPLICATE | Local Trader system prompt already ported by d415db4. |
| `8767801c39cbe4ddd481a82dc71e247d9d454a71` | DUPLICATE | Original September casebook already ported by d415db4. |
| `079ca94dddef4d49069cb0a977c264aeae5f2743` | DUPLICATE | Trader ingest manifest already ported by d415db4. |
| `2e69e024e01f693d52958361f98a85c4316ad4fa` | DUPLICATE | Analysis API re-exports already ported by d415db4. |
| `da67a63251ab2ffb5d20a624772d2cb9a0427365` | DOC_ONLY | Training-TZ cross-reference; retain reference without claiming a trained model. |
| `c592df930b450b29d97acf409a06a1ae2acdf5c6` | SUPERSEDED | Archive cleanup mostly already in floor; this commit also deletes the required Social Farm fixture restored by 2a7d3a2. Do not apply wholesale. |
| `6e5d8b57a9327a8f916c16c42f9eb2549f016642` | DUPLICATE | The removed tracked runtime cache is already absent in floor (git cat-file checked). |
| `94717d52f05233b1449eab806f714b473c8cf838` | SUPERSEDED | Temporary placeholder removed again by 2a7d3a2; absent in floor. |
| `4fb5015f66d85947fe65ebedfa8b292903aff776` | SUPERSEDED | Temporary fixture note removed again by 2a7d3a2; absent in floor. |
| `be7242a3c1652a27fdd027cfb484fe808c2196c3` | SUPERSEDED | Temporary DO_NOT_USE file removed again by 2a7d3a2; absent in floor. |
| `2a7d3a2dd7eca278322a34afa9ca3c69a4ee3c1d` | DUPLICATE | Floor already contains the Social Farm fixture and no temporary placeholders. |
| `392421ce095f00e9133721f911f929ea7f3b3c2f` | EXPERIMENTAL_NOT_FOR_RELEASE | Additional trading observations only; explicitly excluded by no-new-Trader emergency priority. |
| `0b98cf9697e7fa9b1f397693cb37fbc0ccb87e1e` | EXPERIMENTAL_NOT_FOR_RELEASE | Additional trading observations only; explicitly excluded by emergency priority. |
| `b9e00294963b0af9ab7d2a250272caf0c95d7254` | EXPERIMENTAL_NOT_FOR_RELEASE | New buyer-failure trading regime, not a fix needed to boot or use core workflows. |
| `7885ad7f9adf93d92fa33d12f6296d5b40ed36c6` | EXPERIMENTAL_NOT_FOR_RELEASE | Tests for the deferred buyer-failure trading regime; preserve on source ref. |
| `a6762965c5806d5c44462ee1a516dabf6307fe81` | EXPERIMENTAL_NOT_FOR_RELEASE | Rule/corpus addition for the deferred trading regime. |

### PR #61 — `claude/bossman-final-audit-closure-aucx9x`

| Commit | Classification | Decision |
|---|---|---|
| `69df482c6ab94f04b17715d9e2ceb4a695d20590` | REQUIRED | Already in PR61: completion truth, challenge pause/resume, independent OpenHands evidence, bounded stream/recovery, model picker and repeat-Apply fixes. |
| `413d160ec33272efe561f03916116c949934df81` | DOC_ONLY | Already in PR61: historical finding registry and owner-session clustering; claims remain bound to stated older SHAs. |
| `4001b7ac9e1be9981a06f1f6909ec26a86472196` | REQUIRED | Already in PR61: SHA1 explicitly non-security for Git blob identity, preserving independent evidence and Bandit gate. |
| `cbb29919750b5bde168d389934632f4b0b455472` | REQUIRED | Already in PR61: cleanup removes POSIX read-only parent mode so sandbox deletion actually completes. |
| `8c794dad0ab29d624f4efffa817d41e8301ff56a` | DOC_ONLY | Already in PR61: report points to then-current cbb2991, not current release evidence. |
| `c7e75cc3c0a636d61f6fc8fb3e476a210d1320d3` | REQUIRED | Already in PR61: restore tempfile isolation in new tests; make cleanup containment explicit; contains test changes despite earlier docs-only claims. |

### PR #60 — `claude/bossman-final-integration-pass-04nffa`

| Commit | Classification | Decision |
|---|---|---|
| `355a582d3274ef29da345a177f0380c3b8fd4cdd` | REQUIRED | Selectively integrate series-identity correctness for the Trader module already shipped in floor; not a new trading feature. |
| `9fcf9ab5cbcea46574202a52fa3ca69de294a3f8` | REQUIRED | Selectively integrate immutable run provenance; re-evaluate code-SHA discovery and capture failure behavior for installed package. |
| `7aec22b87adf55b93d5828c12f61be8aa8b99483` | EXPERIMENTAL_NOT_FOR_RELEASE | Optional File Intelligence is deferred until boot/core flows work. If integrated later, must carry all following security and route-leak fixes. |
| `0b5aab2afdfbb0c081c65c127ca0ac2631e7472a` | EXPERIMENTAL_NOT_FOR_RELEASE | Contract tests belong to deferred File Intelligence; fake sidecar does not prove real AI File Sorter. |
| `cf020daa1ec72af90ae069b4118544ff7fe6a12d` | REQUIRED | Independent AP-001 no-secret-output and STOP_GRACE lifecycle regressions are valid for core release without File Intelligence. |
| `99803d3de1d74f46e868d1ce4647240b5091b859` | DOC_ONLY | Historical File Intelligence owner guide; live acceptance table was empty. |
| `1a4fe111fc1d19b18c3ecece35e95df51b2064c5` | DOC_ONLY | Historic suite counts/performance measurements; not transferable to PR61 or final release SHA. |
| `9bea81a9bc01e9f0cbc5c0a9994a5a189ce72c1e` | EXPERIMENTAL_NOT_FOR_RELEASE | Required safety companion IF File Intelligence is integrated: hash sidecar before effect and kill process on timeout. |
| `0ba775f3ff3d77b09cccc518f6f484bd080021b1` | DOC_ONLY | Historical owner candidate pointer; not release acceptance. |
| `76b95858fa83773658b7fd6fb66194872ee12f20` | DOC_ONLY | Historical sequential suite results; never combine these with PR61 suites. |
| `a1e633cb3c961302a74bac63da51be16fd08c53b` | EXPERIMENTAL_NOT_FOR_RELEASE | Required companion IF File Intelligence is integrated: register routes once and resolve service from app.state; repeated-setup regression. |
| `e5ccd54c8fea94940f2ebced41bcb583076ac264` | DOC_ONLY | Frozen a1e633c report is source-history evidence only. |

### PR #54 — `build/local-bundle-20260907`

| Commit | Classification | Decision |
|---|---|---|
| `210cc525fd0d571facffaebf60a4456eb2cf267b` | REQUIRED | Selectively integrate UI location/bundle intent and strengthen clean-install proof. Original builder is not a complete installed-product acceptance. |

### PR #59 — `feature/ai-streamer-higgsfield-browser-20260908`

| Commit | Classification | Decision |
|---|---|---|
| `5e8a39d63b87e4fd073119776f4f210d93877975` | DOC_ONLY | Optional AI Streamer architecture, deferred under emergency order. |
| `aea7a2a83cde016dc2c5ae9bab5ad331c8f06bcb` | DOC_ONLY | Optional workflow catalog; no executable acceptance. |
| `dc464131a40247dd1d0a750aa49daea7370c4b2e` | DOC_ONLY | Higgsfield browser contract; does not establish live selectors/account access. |
| `5499908236e3f3ed71f42dc2faed9e21a05b8efd` | DOC_ONLY | Optional OpenHands media engineer design; no permission to expand features. |
| `ce849f4bcd13d365b24b56e65fa3b035559d6362` | EXPERIMENTAL_NOT_FOR_RELEASE | Deferred standalone browser-generation job contracts. |
| `5d7f77a65dc4e18552b79bd90f86c010b891e0cb` | EXPERIMENTAL_NOT_FOR_RELEASE | Fixture-only tests for deferred browser-generation contracts. |
| `63bd277d6c5cb4114968ac0d2878a43be57ce7d1` | DOC_ONLY | Optional mission/acceptance specification; no completed live run. |
| `6eaeb621d9bd11385c7592752d8232721d2c4d51` | EXPERIMENTAL_NOT_FOR_RELEASE | Deferred generation job store; no existing core boot dependency. |
| `134f2f620ce2a3d9fc8862a28af086ebb795ad52` | EXPERIMENTAL_NOT_FOR_RELEASE | Deferred generation-worker orchestration. |
| `85cad480ba66d29b2380d1f636c76742f993097c` | EXPERIMENTAL_NOT_FOR_RELEASE | Deferred media-generation resource router. |
| `01f079b341570be99859619ab673a9adcab5e156` | EXPERIMENTAL_NOT_FOR_RELEASE | Tests for deferred generation job store. |
| `2d104e74aca7e732ec5b4c46495311009f6e541b` | EXPERIMENTAL_NOT_FOR_RELEASE | Tests for deferred resource-aware media router. |
| `8937b93993e7d6f0a3e192a76bf79fbb035d5771` | EXPERIMENTAL_NOT_FOR_RELEASE | Fixture success/challenge tests for deferred worker. |
| `8d9a45685fbb4e81a834ebd2bc3b6c7c7eff4352` | EXPERIMENTAL_NOT_FOR_RELEASE | New rolling content buffer is optional expansion, not release closure. |
| `4a9c58b87fb91d266c94dfe44fde3c3e3266f034` | EXPERIMENTAL_NOT_FOR_RELEASE | Tests for deferred content buffer. |
| `d3d157a1ca837fbbc72ff732c11b9bd4a138898a` | SUPERSEDED | Initial direct Higgsfield adapter is superseded by session-mediated 3582d99; neither is admitted yet. |
| `43aaf9171d16626a921b05e14d87f927b92094dd` | DUPLICATE | Ancestry merge from night convergence, which PR61 already inherits through 45027d3; do not replay an ancestry merge as feature work. |
| `3582d99257f2d9bffc2261fc4bb929efaa4fbbad` | EXPERIMENTAL_NOT_FOR_RELEASE | Session-mediated adapter, selector pack and challenge boundary; must replace initial adapter if optional subsystem is admitted. |
| `e0463b698c898ee6f68f0941e541fe97c0e1af17` | EXPERIMENTAL_NOT_FOR_RELEASE | Quarantine/media-probe gates required with deferred adapter; real local fixtures are not Higgsfield downloads. |
| `ed420c1d772a4e1413f89d6eca3521c486ca5236` | EXPERIMENTAL_NOT_FOR_RELEASE | Submission journal/lease/resume controls required with deferred worker; UNKNOWN must never be re-submitted. |
| `142b815d716a0d962e35b59c59d5ce3f9687f5a7` | CONFLICTING_IMPLEMENTATION | Extends bcc/features/reality.py also changed by PR61 invalid-input fix; preserve PR61 validation if optional observations are later added. |
| `80738753543d9e84df1b6930259bd4a6fe6f69ba` | EXPERIMENTAL_NOT_FOR_RELEASE | Persona canon, prompt pipeline and segment QA are optional new media subsystem. |
| `24f0426ab346467030e8cfb34c71ea0268fce45b` | EXPERIMENTAL_NOT_FOR_RELEASE | Optional drift packet/selector-repair mission; independent OpenHands evidence from PR61 must remain authoritative. |
| `8a07616e0cb959afce676394c28791861d0e794a` | EXPERIMENTAL_NOT_FOR_RELEASE | Optional virtual-clock six-hour soak; no six-hour real-time evidence. |
| `ad1e0aaa4cde71826b855e19cf87852cf4c87448` | EXPERIMENTAL_NOT_FOR_RELEASE | Fixture timing correction plus implementation map; not live acceptance. |
| `f5468559430af964dcfad5ef89e7e390d3f64737` | EXPERIMENTAL_NOT_FOR_RELEASE | Social Farm workflow and fixture fixes belong to deferred optional subsystem. |
| `2fe7c6c7054e2c0febb29dc333100d5f0b321154` | EXPERIMENTAL_NOT_FOR_RELEASE | Security/boundedness corrections required if deferred worker/adapter is later admitted. |
| `fbb2da7396fb4e7ac3eb35285f77e5403599c016` | EXPERIMENTAL_NOT_FOR_RELEASE | Optional Social Farm workflow naming; no core runtime effect. |
| `a3f5a8a22a895ea59b5109f4e10aad92d7e07970` | EXPERIMENTAL_NOT_FOR_RELEASE | Navigate before account identification; required with optional adapter, selectors still unverified live. |
| `4f4fd1cf6c9ca9387dda924978279cf8dde9ac04` | EXPERIMENTAL_NOT_FOR_RELEASE | CAPTCHA during submit/download becomes human challenge; required with optional adapter. |
| `d13e905edd4005dddacc327654a51f22a9359607` | DUPLICATE | Resource-test trailing whitespace also removed by floor 7d7b8e7; no behavior to integrate. |
| `d884a7f5ecbac6fdac4ece9e50c6bb49d8d6d809` | EXPERIMENTAL_NOT_FOR_RELEASE | Social Farm Python testpath packaging adjustment; apply only with optional workflow and verify separately. |
| `ab273e8ff0874d7671625bed2982f43bd423de73` | DUPLICATE | Ancestry merge from night convergence, which PR61 already inherits through 45027d3; do not replay an ancestry merge as feature work. |
| `df09cceb798896a1b7cb2135967fbce313f8bdd1` | DUPLICATE | Ancestry merge from night convergence, which PR61 already inherits through 45027d3; do not replay an ancestry merge as feature work. |
| `9e18426454de5e61d3afd4e27b4dba50a2288aab` | EXPERIMENTAL_NOT_FOR_RELEASE | Extends skip registry scanner to Social Farm; regenerate only when corresponding optional suite is included. |
| `8db8e08a16e8e1c5f2497d16816c1c8464ce8200` | DUPLICATE | Ancestry merge from night convergence, which PR61 already inherits through 45027d3; do not replay an ancestry merge as feature work. |
| `4cd1d16b3ec0a20512c63f04227968b1cc5ae0ff` | DUPLICATE | Ancestry merge from night convergence, which PR61 already inherits through 45027d3; do not replay an ancestry merge as feature work. |
| `21397767ea071d0d434335057ce87c215282c412` | DUPLICATE | Ancestry merge from night convergence, which PR61 already inherits through 45027d3; do not replay an ancestry merge as feature work. |
| `406d71ba232b0a7696daef7467bb11c5475614d5` | DUPLICATE | Ancestry merge from night convergence, which PR61 already inherits through 45027d3; do not replay an ancestry merge as feature work. |
| `9e22269ab21cc75323686aae8b95deb67bfd54eb` | DUPLICATE | Ancestry merge from night convergence, which PR61 already inherits through 45027d3; do not replay an ancestry merge as feature work. |
| `0b5c674b8995f0a88d9935d93b5c0ae59ffa4525` | DUPLICATE | Ancestry merge from night convergence, which PR61 already inherits through 45027d3; do not replay an ancestry merge as feature work. |
| `128324713c140c908a7ad502ed5621e5ea22a6a6` | DUPLICATE | Ancestry merge from night convergence, which PR61 already inherits through 45027d3; do not replay an ancestry merge as feature work. |

## Acceptance boundary

This worktree changed only this ledger and its machine-readable companion. It did
not merge branches, run full release CI, install the final product, or certify any
live provider. The new gateway defect is reproduced with actual conversion methods
and synthetic protocol data; no model/provider call was made. Release readiness
is deliberately not claimed.
