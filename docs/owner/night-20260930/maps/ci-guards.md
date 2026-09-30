# CI and repository guards for a commit on `claude/bossman-1.9-owner-bugtest-20260930`

## Status and method

Everything below was found by reading the files. No tests were run. I also ran the read-only checkers once on HEAD (`b6018ee3`) to get a baseline, and all passed:
- `git diff --check <empty-tree> HEAD -- <root-ci scope>`: clean.
- `tools/skips_registry.py --check`: PASS, 349 entries.
- `tools/ci_secret_scan.py`: PASS.
- `scripts/update_readme_scorecard.py --check`: PASS.
- `tools/render_capability_matrix.py --check`: PASS.

---

## 1. Workflows

### 1a. Always run on a push to this branch (no branch or path filter)

| Workflow file | `name:` (cert name) | What it runs |
|---|---|---|
| `root-ci.yml` | root-ci (shared contracts, learning layer, tools) | Matrix py3.11 and 3.12. Installs **only** `-e .` plus `pytest pytest-timeout psutil httpx pyyaml`. Steps: `pytest tests --timeout=120 --timeout-method=signal`; `scripts/update_readme_scorecard.py --check` (L48); `tools/skips_registry.py --check` (L50); `compileall bossman-core/bossman command-center/bcc learning bossman_shared tools` (L52); `tools/ci_secret_scan.py` (L54); `git diff --check $(empty tree) HEAD -- bossman-core/bossman bossman-core/tests command-center/bcc command-center/tests command-center/ui learning bossman_shared tools tests schemas scripts docs/benchmark .github/workflows/{root-ci,bossman-core-ci,command-center-ci}.yml docs/security docs/intelligence` (L59). Separate `docker-smoke` job: `docker build -f bossman-core/Dockerfile .`, then imports `bossman_shared.cache_observation, learning.trace, bossman_schemas, bossman._shared`. |
| `command-center-ci.yml` | Command Center CI | **test**: py3.11, 3.12 and **3.14**. `compileall command-center/bcc command-center/tests` (L70). Installs Playwright Chromium and ffmpeg, sets `BCC_REQUIRE_BROWSER=1`, then from `command-center/` runs `pytest -q --timeout=180 --timeout-method=signal --cov=bcc --cov-fail-under=72` (L104-105). **core-runtime** (ubuntu and windows, py3.12): a hard-coded list of test files, e.g. `test_coding_tasks.py`, `test_terminal_cli_e2e.py`, root `tests/test_evolution_*.py`, `tests/test_mvcr_prepare.py`, `tests/owner_journeys/test_admin_journeys.py`, `test_route_ladder.py`. **checks**: `ci_secret_scan.py` (L170); `astra_security_gate.py --component command-center` (bandit plus pip-audit, L178); forbidden filenames `git ls-files \| grep -E "(^\|/)\.env$\|\.pem$\|(^\|/)token$\|(^\|/)secret\.key$"` (L191); `node --check` on **every** `command-center/ui/**/*.js` including vendor (L201); `node --test ui/tests/web_designer_autosave.test.mjs` (L205) and `ui/tests/app_view.test.mjs` (L208). **windows-paths** (windows-latest): about 25 test files named explicitly, e.g. `test_discovery.py`, `test_finalize_gate.py`, `test_owner_global_stop.py`, plus three nodeids in `test_video_descriptor_boundary.py`. |
| `bossman-core-ci.yml` | Bossman Core CI | **windows-workspace**: specific core tests. **core**: py3.11 and 3.12, split into 4 groups (security, gateway-context, stage8-14, rest); the file lists are hard-coded, and `rest` = `tests/` minus `--ignore` of the other groups. **coverage**: full core suite plus root `tests/test_evolution_*` with `--cov=bossman_v3 --cov-fail-under=85` (L236). **compile**: `compileall bossman-core/bossman`, secret scan, `astra_security_gate.py --component bossman-core`. |
| `postgres-contracts.yml` | PostgreSQL run contracts | py3.11, 3.12 and 3.14 against a postgres:16 service. Runs `command-center/tests/test_run_provenance_postgres.py` and `test_v5_terminal_run_immutability.py`. The JUnit post-check asserts **exactly 4** `test_postgres_*` cases, and zero skipped, failed or errored cases. |
| `astra-acceptance.yml` | ASTRA acceptance | Portable (ubuntu and windows): `bossman-core/tests/test_astra_remediation.py`, `test_v3_evidence_signing.py`, `test_v3_organization_e2e.py`, `command-center/tests/test_astra_remediation_cc.py`. Runner job: `tools/astra_acceptance.py --profile runner` with two **pinned nodeids**: `test_discovery.py::test_open_port_that_stays_silent_is_not_called_absent` and `test_v21_failure_injection.py::test_provider_failure_retries_are_bounded_and_status_is_honest`. A skip counts as FAIL. |
| `solana-safety-ci.yml` | Solana safety gates | `pytest tests/test_solana_safety.py solana_volume_suite/tests/test_safety_vault.py` |

### 1b. Run on `claude/**` with no path filter
- `bossman-v2-repair.yml` ("Bossman V2 Auto-Repair"): pgvector postgres. Runs `pytest bossman-core/tests` (P0 `-k` subset, then the full suite minus two sandbox files). It opens an auto-PR only on `claude/bossman-control-v03-43igbk`.
- `fable-media-fleet.yml` ("Fable media and Fleet acceptance"): py3.11 and 3.12 with ffmpeg and Chromium. Runs `tools/media_ci_preflight.py`, `tools/media_roundtrip.py`, core fleet tests, `command-center/tests/test_video_studio_*.py` (glob), web designer tests, fable tests, `ci_secret_scan.py` and `git diff --check`.

### 1c. Run on `claude/**` only when the push touches certain paths
All of these are **Windows jobs or long jobs**:
- **`windows-bundle.yml`** ("One-download Windows application", runs up to 75 min): triggered by `command-center/**` excluding `command-center/tests/**` (a list of specific test files is re-included), `bossman-core/**`, `bossman_shared/**`, many `tools/*`, and `tools/release_candidate.json`. It runs `tools/app_icons.py --check`, `build_windows_bundle.py`, `verify_windows_bundle.py`, `scripts/evening_owner_run.py --ci preflight` (the worktree must stay clean), and the installed-product acceptance profile from `tools/acceptance_registry.json`.
- **`installed-product.yml`** ("Installed product chain", windows): triggered by `command-center/**`, `bossman-core/**`, `apps/**`, `pyproject.toml` and `tools/release_candidate.json`, but not by commits that only touch `command-center/tests/**` or `bossman-core/tests/**`.
- **`owner-scenarios.yml`**: triggered by `command-center/bcc/**`, `bossman-core/bossman/**`, `bossman_shared/**`, `tests/owner_scenarios/**`, `tools/scenario_runner.py` and others. Runs `tools/scenario_runner.py --min-green 102`; fewer than 102 green of 110 means FAIL.
- **`windows-stress-100.yml`** ("Windows 100 real checks"): triggered by `command-center/**` **including tests**, and `bossman-core/**`. `tools/windows_100_real.py` takes 100 nodeids from the file lists in `GROUPS` (L36+). A missing file, a listed file with no tests, or any skip or error is FAIL.
- `windows-owner-tasks.yml`: triggered only by `scripts/windows_owner_tasks.py`, `scripts/verify_clean_install.py`, its own yml, and `tools/release_candidate.json`.

### 1d. Not triggered by a push to this branch
`editors-user-safety`, `oss-integrations`, `intelligence-preservation`, `local-bundle`, `shipped-apps`, `human-speed-components`, `v15-economy-ci`, `v16-foundation-ci`, `v16-integration-ci`, `v17-pit-ci`, `motion-studio`, `evening-residual-safety`, `closure-checkpoint`, `apply-sandbox-runtime-hardening`, `repair-media-roundtrip-test`, `release-certification` (dispatch only). Each is limited to other branch names, `release/**`, pull requests, or manual dispatch.

If a PR is opened from this branch, these run in addition: `bossman-benchmark`, `intelligence-preservation`, `local-bundle`, `shipped-apps`, `fable-media-fleet`, and, when their paths match, `editors-user-safety`, `v16-foundation`, `v17-pit` and `motion-studio`. `v16-integration-ci` runs only for a PR whose base is `release/bossman-owner`.

### 1e. Which workflows are required
- **`tools/exact_sha_certify.py` L48 `DEFAULT_REQUIRED`** (the displayed `name:` must match exactly): root-ci, Bossman Core CI, Command Center CI, Bossman V2 Auto-Repair, ASTRA acceptance, Solana safety gates, Fable media and Fleet acceptance, One-download Windows application, Windows owner run — light, medium and super-long task, Windows 100 real checks, Owner scenarios (integrated, not unit tests).
  - PostgreSQL contracts and Intelligence Preservation are **not** on this list.
  - A required workflow with no run is MISSING, and the verdict is `NOT_CERTIFIED` or `INSUFFICIENT_EVIDENCE`.
- **`tools/owner_facing_branches.json`**:
  - `must_be_covered`: `integrate/bossman-1.7-unified-20260925`, `release/bossman-owner`, `claude/bossman-final-convergence-hu2702`, `night/v7-convergence-20260908`.
  - `workflows`: the 8 branch-restricted files (windows-bundle, windows-owner-tasks, oss-integrations, installed-product, owner-scenarios, bossman-v2-repair, fable-media-fleet, windows-stress-100).
  - `release_candidate_marker`: `tools/release_candidate.json`.
- **`tools/release_candidate.json`**: editing this file *declares an exact-SHA release candidate* and starts every path-filtered Windows and long job. It must contain `candidate_label` and **must not** contain a `sha` key (`tests/test_required_workflows_are_reachable.py:216`). **Do not touch it unless you mean to declare a candidate.**

---

## 2. Repo-wide guards (static or bookkeeping) that a new commit can break

### 2.1 Skips registry
- **Checked by:** `tools/skips_registry.py --check` (root-ci L50) and `tests/test_skips_registry.py:13`. The registry is `docs/testing/SKIPS_REGISTRY.md`.
- **What it asserts:**
  - It walks every `pytest.skip / skipif / importorskip / mark.skip*` call (found by AST) in `test_*.py` files under `command-center/tests`, `bossman-core/tests`, `tests`, `apps/social-farm/tests` and `apps/file-commander-mini/tests` (recursively), plus the top-level `conftest.py` of each.
  - The generated text must match the committed file **byte for byte**, and every skip must have a reason.
- **Rule:**
  - Each row records `file:LINE`. **Any edit that shifts lines in a test file containing a skip makes the registry stale** and root-ci goes red. So do adding a skip, or a new test file with a skip.
  - Always regenerate with `python tools/skips_registry.py`, which writes LF. Rows are in `ast.walk` order, so never hand-edit.
  - Every skip needs `reason=`.

### 2.2 Whitespace and line endings
- **Checked by:** `git diff --check` against the empty tree over the root-ci scope listed in 1a, so it checks every file in those paths, not just the diff.
- **What fails:** trailing whitespace, including a CR from CRLF (`cr-at-eol` is off by default), space before tab in indentation, and blank lines at the end of a file.
- **What `.gitattributes` exempts:** `command-center/ui/icons/** binary`; `ui/icon.svg -text`; `docs/owner/evidence/** -text`; `docs/v8/owner-final-run/** -text`; `.agents/skills/** -text`; `command-center/bcc/skills_catalog/** -whitespace -text`. There is **no** global `text=auto`.
- **Rule:** LF only, no trailing spaces, no trailing blank lines, in `bcc`, `ui`, `tests`, `tools`, `scripts` and the other scoped paths.

### 2.3 Secret scan and forbidden files
- **Checked by:** `tools/ci_secret_scan.py`, which runs in root-ci, CC-CI, core-CI and fable. It scans **all tracked plus untracked, non-ignored** files.
- **What fails:**
  - Provider key patterns: `sk-…`, `sk-ant-…`, `sk-or-v1-…`, `AIza…`, `xox…`, Telegram `\d{8,10}:[\w-]{35}`, JWT, `ghp_`, `AKIA…`, private-key headers, `seed phrase:`. <!-- ci-secret-scan: allow -->
  - `password = "8+ chars"`, including annotated forms. Unquoted `…PASSWORD=value`. <!-- ci-secret-scan: allow -->
  - Forbidden filenames: `.env`, `.env.*` (except `.example`, `.sample`, `.template`), `*.pem`, `*.p12`, `*.pfx`, `*.key`, `id_rsa*`.
  - **Any file over 2,000,000 bytes that is not a skipped media/binary type** is reported as "unscannable oversized file". The skipped types are png, jpg, webp, gif, ico, pdf, sqlite, db, gguf, safetensors, woff, ttf, webm and mp4. Zip files are opened and scanned.
  - Shannon-entropy detector (H ≥ 4.0, token ≥ 24 chars from `[A-Za-z0-9+_-]` with both letters and digits), **only in `.py .yml .yaml .toml .ini .cfg .env .sh`**. A line is skipped if it contains words such as sha256, hash, uuid, `https://`, `import `, nonce or signature, or if the token contains a dictionary hint such as test, fake, example, value, config, default, bossman or claude.
- **Rule:** fake keys and random literals in tests need the same-line comment `# ci-secret-scan: allow` (see HEAD commit b6018ee3). No `.env` files or files named `token` in git. No generated file over 2 MB.

### 2.4 SAST and dependency audit
- **Checked by:** `tools/astra_security_gate.py`. It runs `bandit -r command-center/bcc --severity-level high --confidence-level medium` (the core run scans `bossman-core/bossman`, `bossman_v3` and `bossman_shared`). **Any** finding fails; there are zero `# nosec` markers in `bcc`. It also runs `pip-audit` on the installed environment, and any vulnerability fails.
- **Rule:** in `bcc`, no `hashlib.md5/sha1` without `usedforsecurity=False`, no `subprocess` with `shell=True` on a dynamic string, no `verify=False`, no `tarfile.extractall` without a filter, no paramiko `AutoAddPolicy`, no telnetlib/ftplib. None of these appear in `bcc` today. New dependencies must have no known CVE.

### 2.5 Compile and syntax compatibility
- **Rule:** Python code must be valid for **3.11** (root-ci, core-CI) and also run on **3.14** (CC matrix). So no 3.12-only syntax such as PEP 695 `def f[T]` or reusing the same quote type inside an f-string.
- **Rule:** no `asyncio.shield` anywhere in `bcc` (`test_single_flight.py:212`); use `bcc.single_flight.await_shared`.
- `compileall` also covers `tools/`, `learning`, `bossman_shared` and `bossman-core/bossman`.

### 2.6 JavaScript
- **`node --check`** runs on every `.js` under `command-center/ui`, vendor included. There is no `setup-node` step, so the runner's default Node is used. Files are ES modules with no `package.json`.
- **There is no ESLint, Prettier or package.json in the repo.**
- **`ui/tests/app_view.test.mjs`**: slices `app.js` from `async function renderPage(` up to `\nfunction refresh()` and runs it in a `vm` context whose only globals are:
  `PAGE_BY_ID, currentPage, currentParams, renderToken, lastRendered, ctx, el.view, retainAppFrame, replace, mark, schedulePreload, syncTopStats, window.scrollTo, console`.
  **Rule:** keep both function names and their order in `app.js`, and do not make `renderPage` call any new global.
- **`ui/tests/web_designer_autosave.test.mjs`**: imports `../pages/web_designer.js` and `../api.js` in Node with only stub globals: `window.{addEventListener, innerWidth}`, `document.{activeElement, addEventListener, getElementById}`, `localStorage`.
  **Rule:** `web_designer.js`, `api.js` and everything they import must not touch other DOM globals at load time, and must keep the exports `autosaveState, flushSave, scheduleSave, sendEdit, attachEditor, SAVE_DELAY_MS, acceptsPickerMessage`.
- `command-center/tests/test_web_designer_viewport.py` runs `node --test test_web_designer_viewport.mjs`, which loads `ui/pages/web_designer_viewport.js` **as a `data:` URL**.
  **Rule:** that module must have no relative imports and must keep its exports `VIEWPORT_PRESETS, viewportSettings, parseViewport, serializeViewport, viewportStorageKey, loadViewport, saveViewport, viewportGeometry`.
- The other `ui/tests/*.mjs` and `*.cjs` files (objectives_wizard, video_studio_state, video_workspace, web_designer_create, `*_browser.cjs`) **are not run in CI**.

### 2.7 UI page registry (`command-center/ui/pages/index.js`)
- **`test_v6_lazy_pages.py:35`** (Chromium):
  - At least 25 pages, with unique `id`s.
  - The static fields of each `lazyPage({...})` must **equal** the module's non-function exports.
  - Allowed static keys: `{id, title, icon, nav, section, sweep}`. Allowed function exports: `{render, onEvent}`. `render` must exist.
- **`test_v6_lazy_pages.py:68`**: the first render may load at most **6** modules from `/pages/` (excluding `_*.js` and `index.js`), and `objectives.js` / `trading_lab.js` must not load. **Rule:** never import page modules statically from `app.js` or from the landing page.
- **Regex parsers** used by `scripts/ui_acceptance_sweep.py:page_routes` and by `tests/test_installed_ui_sweep.py:52`, `test_autonomy_api.py:149`, `test_jeff_2_insights.py:567` and `test_motion_studio_ui.py:174`:
  - `lazyPage\(\{(.*?)\}\s*,` and `lazyPage\(\{ id: '<id>'[^}]*\},\s*\(\) => import\('\./<file>\.js'\)`
  - **Rule:** write each entry as `lazyPage({ id: '…', title: '…', icon: '…', nav: 'primary'|'more', section: '…' }, () => import('./x.js'), (m) => m.default)`. Use single quotes, put `id` first after `{ `, no `}` inside the metadata, and single-quoted strings in a `sweep: [...]` array. Sweep routes must not repeat. The module must `export default` an object with exactly these static fields. The per-page tests also require the page to end with `export default XPage;\n` and contain no `console.log` or `innerHTML` (autonomy and jeff_insights only).
- **`test_ux_navigation_shape.py`**:
  - `section` must be one of `main, work, studio, apps, brains, system`.
  - No two sidebar entries may have the same title (exception: `NAV_DUPLICATES` in `app.js:~108`).
  - `video-studio, web_designer, trading_lab, browser, coding` stay in the sidebar.
  - The phone bar has exactly 5 entries.
- **`test_ux2_pages_sweep.py:35`** (every `window.__bxPages` entry, in Chromium). Each page must render with:
  - no "Повторить" retry panel;
  - **no console errors**, except 404/501/503 and `net::ERR_` noise;
  - a name (text, `aria-label` or `title`) on every visible button;
  - no mojibake;
  - buttons whose labels start with `Новый|Создать|Добавить|Настроить|Подключить|Импорт` actually opening `#modal-root .modal` that closes on Esc, or navigating.
  At 390 px width (L102) there must be no horizontal overflow.
- Icons: an unknown `icon` name silently falls back to `ICONS.info` (`ui/components.js:131`). No test checks icon names.

### 2.8 `index.html` and shell pins
- `tests/test_installed_product_chain.py:173`: the count of enabled `<button>`, `<a href>`, `<input>` and `<select>` after `id="shell"` must equal `tools/installed_product_chain.py:80 SHIPPED_SHELL_CONTROLS = 8`, and `<main … id="view"…></main>` must be empty. **Rule:** if you add or remove a shell control, update the constant in the same commit. Editing that tool also starts installed-product.
- `test_intro_splash.py:17`: `<script src="intro.js"></script>` must come before `src="app.js"`. `ui/intro/bossman-intro.webm` must stay under 3 MB, and `setup.py` / `MANIFEST.in` must include `.webm`.
- `test_login_token_not_offered_to_password_manager.py:13`: the `<input id="login-token"…>` tag has `type="text"`, contains `-webkit-text-security: disc`, and has `autocomplete="one-time-code"`.
- `test_audit_sec_rc19.py:~255`: no `type=password` inside `#login-form`, `login-token` has class `token-mask`, and `style.css` contains exactly `.token-mask { -webkit-text-security: disc; }`.
- `test_login_token_file_hint.py:28`: `app.js` contains `api.loginHint()` and `Токен лежит в файле`; `index.html` contains `id="login-hint"`.
- `tests/test_app_icon_assets.py:124`: every `rel="icon"` and `apple-touch-icon` href, and every icon in `manifest.json`, must exist; no icon may be `maskable`. Do not touch `ui/icons/**`; `tools/app_icons.py --check` runs in windows-bundle.

### 2.9 CSS state classes
- **Checked by:** `test_owner_control_liveness.py:84`.
- **What it asserts:** every class toggled as `classList.add('x')` or `classList.toggle('x')` (single-quoted, `[a-z0-9-]`) in any `ui/**/*.js` outside tests must have a `.x` rule somewhere: in `ui/**/*.css`, in a `<style>` block, or in a CSS-looking template literal.
- Also: `_ui.js` must keep `is-loading` and `aria-busy`, and `web_designer.js` and `apps.js` must show a busy signal.
- There is **no** ban on inline styles, raw `fetch` or `innerHTML` across the UI. `jeff.js` is the exception (`test_jeff_ux_isolation.py`): only `/api/jeff/*` calls, no `./api.js` import.

### 2.10 AST guards over the whole backend
| Test | Rule |
|---|---|
| `test_no_private_fields_in_events.py:23` | No `*.emit(...)` or `*._log(...)` call anywhere in `bcc` may pass keyword arguments named `messages, prompt, system_prompt, api_key, api_key_enc, cookie(s), token, password, secret, authorization`. |
| `test_no_direct_completed_writes.py:54` | No `update(tasks…).values(status="completed")` and no `_finish(_, _, "completed")` outside `finalize.py`. |
| `test_single_flight.py:212` | No `asyncio.shield` in `bcc`. |
| `test_v23_memory_single_writer.py:88` | Only `chunking_v22` is used as a chunker. |
| `test_cu_participant_perimeter.py:50` | `bcc/pit/*.py` must not import `bcc.tools/engine/features/approvals/api` or `bossman.computer_operator`, and must not contain the string literals `/api/computer`, `/api/approvals` or `/api/tasks`. |
| `test_web_research_flag.py:555` | `bcc/features/web_research/*.py` code must not use `httpx.AsyncClient, aiohttp, RobotFileParser, urllib.request, requests.get`. |
| `bossman-core/tests/test_stage13_hostexec_redteam.py:74` | No `shell=True`, `os.system`, `os.popen` or `create_subprocess_shell` in `bossman-core/bossman`, except `projects/runner.py`. |
| `tests/test_product_route_guard.py:85` | `tools/youtube_trader_ingest*.py`, `k1m6a_youtube_batch.py` and `tools/motion_studio/*.py` must not contain `api.openai.com`, `api.anthropic.com`, `generativelanguage.googleapis`, `import openai` or `import anthropic`. |
| `bcc/features/__init__.py:load_features` | Every non-`_` module in `bcc/features` is **imported at startup**, so an import error breaks the app. A `FEATURE` router is mounted automatically under `/api` with `require_token`. There is no separate route inventory or auth-registry test; `/api/command-bar` builds its catalog from the real routes (`test_command_bar.py:82`). |

### 2.11 Root test environment and dependencies
- `tests/test_root_suite_stays_within_its_environment.py:66`:
  - A root `tests/test_*.py` module must not import `sqlalchemy, aiosqlite, fastapi, uvicorn, starlette, bcc, playwright, pytest_asyncio` at module level, directly or through a script it loads with `spec_from_file_location`.
  - There is no `pytest-asyncio` in root-ci, so **no `async def` tests in root `tests/`**. Put them in `command-center/tests`.
- `tests/test_dependency_pinning.py:67`: every `requirements*.txt` anywhere must pin with `==`.
- `tests/test_optional_dependency_declared.py:94`: any `pip install X` string in non-test `.py` must name a package declared in some `pyproject` dependency list or `requirements*.txt`.
- Packaging (`command-center/setup.py`, `MANIFEST.in`):
  - The wheel copies `ui/**` only for `.html .js .css .svg .png .ico .json .webm` (and `.md` under vendor). `ui/tests` is excluded.
  - Non-`.py` data under `bcc/` ships only through `package-data` (currently only `skills_catalog`).
  - **Rule:** a new UI asset type (`.mjs`, `.woff2`, `.webp`) or new `bcc` data file will be missing from the installed product unless setup/pyproject are updated.

### 2.12 Workflow meta-guards (only matter if you edit `.github/workflows`)
- `tests/test_required_workflows_are_reachable.py`: every `DEFAULT_REQUIRED` workflow must be reachable on push for every `must_be_covered` branch. Every branch-restricted required workflow must be listed in `owner_facing_branches.json`. Every path-filtered required workflow must include `tools/release_candidate.json` in its paths.
- `tests/test_windows_workflows_cover_owner_branches.py:144`: every `python tools/*.py` or `python scripts/*.py` a declared workflow runs must be inside its own `push.paths`.
- `tests/test_exact_sha_certify.py:96`: the `DEFAULT_REQUIRED` names must match `name:` in the workflow files, so do not rename workflows.
- `test_root_suite_stays_within_its_environment.py:96`: the root-ci pip line must still contain `pytest pytest-timeout psutil httpx pyyaml` and must not contain `sqlalchemy`, `pytest-asyncio` or `playwright`.
- `tests/test_acceptance_registry.py`: every module in `tools/acceptance_registry.json` (18 `command-center/tests` modules, each with a per-module minimum count, 49 in total) must appear in the windows-bundle push filter. **Deleting tests from those modules below their minimum fails the Windows gate.**

### 2.13 Lists of test files and nodeids pinned elsewhere (do not rename or delete)
- File lists in `command-center-ci.yml` (core-runtime and windows-paths), `bossman-core-ci.yml` group paths, `astra-acceptance.yml`, `fable-media-fleet.yml`, and `postgres-contracts.yml` (exactly 4 `test_postgres_*` cases).
- `tools/astra_acceptance.py` PROFILES (two nodeids) and `tools/windows_100_real.py` GROUPS.
- Evidence references in `docs/v8/CAPABILITY_MATRIX.json`: `tests/test_capability_matrix.py:48` checks that the named test exists, and the `.md` must match `tools/render_capability_matrix.py --check`.

### 2.14 Docs and owner-format guards
- `README.md`:
  - The scorecard block between the `BOSSMAN_LIVE_SCORECARD_*` markers must match `docs/benchmark/current-scorecard.json` (`update_readme_scorecard.py --check`).
  - Every `python <path>` inside a README code block must exist (`tests/test_readme_commands_are_real.py`).
  - `Start-Bossman.cmd`, `Evening-Test.cmd` and `OWNER_ACCEPTANCE.md` must still be mentioned.
- `docs/owner/bossman_critical_errors.json`: VERIFIED entries pin the sha256 of `docs/owner/evidence/**` (`tests/test_import_operational_lessons.py`).
- `docs/skills/voltagent-agent-skills.lock.json` pins `.agents/skills/**`, and `bcc/skills_catalog/*/provenance.json` pins the imported skills (`test_skill_catalog.py:104`). Treat these as byte-frozen.
- `tests/owner_scenarios/test_owner_scenarios.py`:
  - `BOARD_SIZE = 110`.
  - The registry `owner_scenarios.json` and `sr.IMPLEMENTATIONS` must match.
  - A frozen table `EXPECTED_WHEN_CAPABLE` must hold.
  - `owner_gap` declarations must match the non-green rows.
  - **A product change that alters an owner-scenario outcome, or closes a gap, requires updating the registry or the table.**

### 2.15 Coverage floors
- CC `bcc` must stay at or above **72%**; the measured baseline was 76% on 2026-09-05 (`docs/testing/COVERAGE_BASELINE.md`). Large new `bcc` modules without tests can drop it below the floor.
- Core `bossman_v3` must stay at or above **85%**.

---

## 3. Static tooling
- **Not configured:** ruff, flake8, mypy, pylint, black, isort, ESLint, Prettier. There is no `setup.cfg`, `tox.ini` or `.pre-commit-config`, and no `package.json`. The pyprojects configure only pytest: `asyncio_mode=auto` for CC and core; the root has no pytest config.
- **What is configured:**
  - `python -m compileall` (see 2.5);
  - bandit at HIGH severity plus pip-audit (`tools/astra_security_gate.py`);
  - `tools/ci_secret_scan.py`;
  - `git diff --check`;
  - `node --check` on all UI `.js`;
  - `node --test` on exactly two `.mjs` files, plus one more run from inside pytest;
  - `pytest-timeout` limits of 180 s per test (CC) and 120 s (root and core).
- **`BCC_REQUIRE_BROWSER=1`** in CC-CI: Chromium is installed, so browser tests are expected to run for real.

---

## 4. Checklist for implementers
1. After editing any test file that contains a skip call, or adding a skip, run `python tools/skips_registry.py` and commit `docs/testing/SKIPS_REGISTRY.md`.
2. LF only, no trailing whitespace, no trailing blank lines at the end of files.
3. No real-looking keys. Mark fixtures `# ci-secret-scan: allow`. No `.env`, `*.key` or `*.pem` files, no files named `token`, and nothing over 2 MB.
4. In `bcc`, avoid bandit-HIGH patterns: md5/sha1, `shell=True`, `verify=False`, bare `extractall`.
5. Python must be valid for 3.11 and run on 3.14. No `asyncio.shield`.
6. New page: add one `lazyPage` entry in the exact single-quote format. The module's default export must have the same static fields and `render` (plus optional `onEvent`) and nothing else. `section` must be one of the six. The title must be unique in the sidebar. Opener buttons must open a modal that closes on Esc. No console errors. It must fit a 390 px width.
7. Every class the JS toggles with `classList.add` or `classList.toggle('x')` needs a `.x` CSS rule.
8. If shell controls in `index.html` change, update `SHIPPED_SHELL_CONTROLS` in the same commit. Keep `renderPage` and `refresh` in `app.js`, and keep the pinned login and intro markup.
9. `bus.emit` must not carry private keyword arguments. Task completion is written only through `finalize.py`. PIT modules stay isolated.
10. Root `tests/` must use no `async def` and no `bcc`, `fastapi` or `sqlalchemy` imports; those tests belong in `command-center/tests`.
11. Do not rename or delete test files or nodeids that CI lists by name (2.13).
12. Do not touch `tools/release_candidate.json`, the workflow `name:` lines, `ui/icons/**`, pinned evidence, skills catalogs or the scorecard JSON unless that is the intent.
13. Before editing a UI or `bcc` file, run `grep -rn "<basename>" command-center/tests tests`. Many tests pin exact source strings: `index.html` in about 15 test files, `app.js` in about 11, `pages/index.js` in 9, `api.js` in 9.
14. Any push touching `command-center/**` or `bossman-core/**` on `claude/**` also starts the Windows bundle (up to 75 min), installed-product, owner-scenarios (bcc changes only) and Windows-100 jobs, so the code must also work on Windows.