# LOCAL MODEL WORKFLOW — V3 / V4 / V5 (loopback, no cloud required)

Base: `kimi/final-residual-closure-20260906` @ `c1e186b` (tracks
`origin/integration/continuity-steward-closure-20260906`).
All steps run on the owner machine against loopback endpoints with temporary
state. Nothing here sends real external messages, moves real money, or touches
production data.

## 0. Host prerequisites (verified 2026-09-06, Windows 11, i9-14900HX, 16 GB)

| Item | Verified value |
|---|---|
| OS / CPU / RAM | Windows 11 Home 10.0.26200 / i9-14900HX 24C/32T / 16 GB |
| GPU | RTX 4060 Laptop 4 GB + Intel UHD |
| Python / Node / FFmpeg | 3.14.3 / v25.8.2 / 8.1-full_build-www.gyan.dev (repo expects exactly this FFmpeg) |
| Chromium (Playwright) | `C:\Users\timur\AppData\Local\ms-playwright\chromium-1234\chrome-win64\chrome.exe` |
| Ollama service | **`http://127.0.0.1:11435`** — port `11434` on this host is held by a silent `svchost` (WSL2 forwarder). `bcc/discovery.py` documents this trap. Always probe before assuming `:11434`. |
| Local models (`ollama list`) | `qwen2.5:7b` (4.7 GB), `qwen2.5-coder:14b` (9.0 GB), `llama3.2:latest` (2.0 GB) |

Windows shell notes: set `$env:PYTHONIOENCODING='utf-8'` in every PowerShell
session (repo help/CLI text is Russian; cp1252 crashes argparse help).
Pytest leaves a `pytest-current` symlink-cleanup `PermissionError` (WinError 5)
in `atexit` noise — it is NOT a test failure.
No Developer Mode / no `SeCreateSymbolicLinkPrivilege` on this host:
symlink-dependent tests must `pytest.skip("SKIP_HOST: ...")` (repo convention,
see `command-center/tests/test_plugins_adapter.py:208`), never fail.

## 1. Isolated environment (mandatory)

```powershell
$env:PYTHONIOENCODING='utf-8'
# separate worktree (never test in the owner's working checkout)
git worktree add C:/Users/timur/AppData/Local/Temp/opencode/kimi-wt kimi/final-residual-closure-20260906
# separate venv reusing installed packages
python -m venv C:/Users/timur/AppData/Local/Temp/opencode/<run-id>/accept-venv --system-site-packages
<run-id>/accept-venv/Scripts/python.exe -m pytest --version
```

Test isolation per run: separate worktree + separate venv + temporary DB
(`BCC_DATA_DIR` below) + temporary media/sites/browser profile + loopback only.
Mock external side effects unless live is explicitly authorized.

## 2. Local model endpoint (Ollama)

```powershell
ollama list                       # expect qwen2.5:7b, qwen2.5-coder:14b, llama3.2
curl.exe -s -m 5 http://127.0.0.1:11435/   # must print: Ollama is running
curl.exe -s -m 5 http://127.0.0.1:11434/   # expected: SILENT (svchost trap) -> do NOT use
ollama ps                         # models load on demand; nothing must be preloaded by tests
```

Gateway wiring (`bossman-core/bossman/gateway/config.py`):
default Ollama base is `http://127.0.0.1:11434`; override with service root
(`/v1` is stripped automatically, request paths already contain it):

```powershell
$env:OLLAMA_HOST='http://127.0.0.1:11435'
```

Canonical local-first alias is `bossman-smart`: local `ollama` (priority 10) +
cloud `openrouter` (priority 100). PRIVATE/LOCAL_ONLY workloads must resolve to
the `ollama` backend; if no local model is available the run must end in a
truthful blocker — never silently fall back to cloud (covered by
`bossman-core/tests/test_gateway_cloud_policy.py`).

Recommended local assignments (cheap, fits 16 GB host):
coding/tool loop → `qwen2.5-coder:14b`; chat/triage → `qwen2.5:7b`;
overflow/smoke → `llama3.2:latest`. Keep one model loaded at a time.

## 3. Start Command Center locally (normal launcher)

```powershell
$env:BCC_DATA_DIR='C:/Users/timur/AppData/Local/Temp/opencode/<run-id>/bcc-data'
cd command-center
<run-id>/accept-venv/Scripts/python.exe -m bcc.app --host 127.0.0.1 --port 8878
```

UI: `http://127.0.0.1:8878/` → login form (`#login-token` + `#login-submit`,
server prints/owns the token; token is loopback-only, never logged).
Record launch path, SHA, OS/scale, and screenshots for every journey
(tier `USER_UI_LOCAL_FIXTURE`). `MODEL_TEXT != PROOF`: green UI is valid only
with an independently verified artifact.

## 4. V3 — execution truth (local)

What V3 proves: an effect happened iff independently verified post-state says
so; approvals bind identity + exact arguments at effect time.

Deterministic gate (zero model cost):

```powershell
cd <worktree>
accept-venv/Scripts/python.exe -m pytest command-center/tests/test_mission010_execution_truth.py `
  command-center/tests/test_fable_approval_revocation.py `
  command-center/tests/test_fable_crash_after_effect.py `
  command-center/tests/test_secrem_f013_approval_identity.py `
  command-center/tests/test_no_direct_completed_writes.py `
  command-center/tests/test_finalize_gate.py -q -p no:cacheprovider
```

Visible-UI attack (real Chromium, temp DB, local model route):
deny approval → effect must NOT occur; change arguments after approval → fresh
authorization required; crash after external effect → resume parks the unknown
outcome, never replays the irreversible effect; stale evidence cannot turn UI
green. Drive via the mission console (`#/missions`) exactly as
`command-center/tests/test_mission_console.py` does (Playwright `_login`).

## 5. V4 — Continuity (local)

What V4 proves: dependent task B never visibly starts before A is verified;
restart resumes from LAST VERIFIED STATE; replanning preserves obligations.

Deterministic gate:

```powershell
accept-venv/Scripts/python.exe -m pytest tests/test_epoch4_mission_ir.py tests/test_epoch4_metrics.py `
  command-center/tests/test_epoch4_mission_ir_bridge.py `
  command-center/tests/test_ux2_restart_durability.py `
  command-center/tests/test_continuity_desktop_ui.py -q -p no:cacheprovider
```

Local long-mission soak (ramp only while gates hold, §8):
50 → 100 → 200 tool actions against the local `qwen2.5-coder:14b` route,
pausing/restarting mid-run; resume must continue from verified state without
duplicate irreversible effects. Recipe/skill reuse only when current
preconditions match; Context OS must use the smallest sufficient context
(compare clean mission vs history-polluted mission).

## 6. V5 — Steward (LOCAL TEST OBJECTIVES ONLY)

Never activate standing objectives on real owner data to satisfy a checklist.
`ObjectiveStore` is a plain SQLite file — point it at temp storage:

```python
from pathlib import Path
from bossman_shared.objective_store import ObjectiveStore
store = ObjectiveStore(Path(tempfile.mkdtemp()) / "objectives.db")
st = store.create(spec, lifecycle="DRAFT")   # always DRAFT until explicitly activated
```

Lifecycle: `DRAFT` → `ACTIVE` → `PAUSED` / `EXPIRED` / `REVOKED` (lifecycle and
condition are separate fields; `ACTIVE` + `UNKNOWN` is legal and UNKNOWN must
never render green). Invariants: `PROPOSAL != AUTHORIZATION`;
`MISSION COMPLETION != SUSTAINED OBJECTIVE HEALTH`; a healthy unchanged
objective produces `MODEL_CALLS=0` / `UNNECESSARY_MODEL_CALLS=0`.

Deterministic gate (golden missions H01–H10 + stores):

```powershell
accept-venv/Scripts/python.exe -m pytest tests/test_v5_golden_missions.py tests/test_v5_admission.py `
  tests/test_v5_objective_store.py tests/test_v5_observers.py tests/test_v5_recovery.py `
  tests/test_v5_intelligence_preservation.py tests/test_epoch5_objective_spec.py `
  tests/test_intelligence_preservation_gate.py -q -p no:cacheprovider
```

H-map (what each golden mission exercises locally):
H01 deviation→proposal→authorization→mission→verified→green;
H02 no change→no proposal, no model call;
H03 out-of-grant capability refused, never published;
H04 crash between deviation and receipt never duplicates an effect;
H05/H05b revoke/expiry while queued denies the effect at the boundary recheck;
H06 duplicate/out-of-order events admit at most one intent.
UI-visible subset (where exposed): create objective → DRAFT → Activate →
deviation creates a *proposal* (not authority) → approve/admit → verified
mission → fresh re-observation → Pause/Revoke/Expire blocks further effects.

## 7. Video Studio + Web Designer (local fixtures, real FFmpeg)

```powershell
accept-venv/Scripts/python.exe -m pytest command-center/tests/test_video_studio_render.py `
  command-center/tests/test_video_studio_cfr_frames.py command-center/tests/test_video_export_receipt.py `
  command-center/tests/test_web_designer.py command-center/tests/test_web_designer_source_fidelity.py `
  command-center/tests/test_web_designer_viewport.py command-center/tests/test_web_designer_sandbox_ui.py -q -p no:cacheprovider
```

Known real bug (open, found 2026-09-06 on SHA `6fcc406`):
`test_rational_range_keeps_selected_final_source_frame[2-4]` —
`StudioError: Clip extends beyond source duration` (`bcc/video_studio/model.py:236`).
Zero-tolerance `source_out > duration_ticks` rejects a legitimate encode:
MP4 container quantization makes probed duration 467 ticks short of ideal.
Fix direction (code, not test): bounded one-frame container slop in
`validate_project`; hostile retest must prove genuinely out-of-range clips are
still rejected. UI repro required before closure (import → trim → export flow).

## 8. Safe stress + resource gates (STOP, do not OOM)

Ramp: 1 → 2 → 4 workers; 50 → 100 → 200 actions; 1000 bounded duplicate
events; 15-min soak first, 60-min only if stable. STOP escalation when:
RAM > 85%, free RAM < max(1 GiB, 15%), free disk < max(5 GiB, 10%), thermal
throttling, or owner loses interactive control. Track PID / creation_time /
parentage for every test-owned process (watchdog); close only proven-owned
processes gracefully — never mass-kill python/node/chrome/edge/ffmpeg/ollama,
never stop system services/AV/VPN/SSH/IDE/DB-model servers.

## 9. Intelligence preservation (same-model, paired, cheap-only)

Gate harness: `tools/intelligence_preservation_gate.py` +
`tests/test_intelligence_preservation_gate.py` (deterministic part runs free).
Live part: same held-out items × RAW / SYSTEM / CONTEXT / FULL BOSSMAN on
`z-ai/glm-5.3` (OpenRouter) or local `qwen2.5-coder:14b` ($0).
Required: `CORE_INTELLIGENCE_RETENTION >= 0.98` **relative** to RAW (ratio, not
percentage points); tool-selection and schema deltas reported per category,
never averaged away. Tiny samples are smoke only → `INSUFFICIENT_EVIDENCE`,
never PASS. Never fabricate `intelligence-preservation-current.json`.
Session LLM budget: **$5 hard cap incl. overhead** — every completion's
`usage.cost` is summed into the run log; local/own-model calls preferred.
Canonical IDs (models.dev 2026-08-26, verified live 2026-09-06):
provider `zhipuai`, model `zhipuai/glm-5.3`, OpenRouter route `z-ai/glm-5.3`,
1M context / 131K output, tools+reasoning+structured+temperature.

## 10. Logging + audit (per run, OUTSIDE the repo)

`bossman-acceptance/<timestamp>-<sha>/`:
`manifest.json` (SHA, tree hash, dirty state, env tier), `glm-identity.json`,
`commands.jsonl` (cmd, cwd, start/end, exit, pass/fail/skip, redacted secrets),
`results.json`, `resources.jsonl`, `process-before/after.json`, `junit/`,
`artifacts/` (screenshots), `failures.md`, `OWNER_REPORT_RU.md`.
Tiers: LIVE / IN_PROCESS / MOCK / NOT_RUN — MOCK is never promoted to LIVE.
No secrets/prompts/cookies/private records in logs (repo secret-scan rejects
`sk-or-v1-*`; the OpenRouter test token lives in env only).

## 11. Bug loop + delivery

UI REPRO (+screenshot) → TRACE → ROOT CAUSE → MINIMAL CODE FIX →
AUTOMATED REGRESSION → SAME UI FLOW AGAIN → POST-STATE VERIFY →
COMMIT → FETCH → PUSH (never force-push, never overwrite others' branches;
`git fetch` before every push). Forbidden: lowering coverage, broad skips,
xfail on real bugs, weakening verifiers/approvals/security/Treasury,
mock-as-live, editing expectations merely to go green.
Final: freeze ONE SHA, re-run ROOT/CORE/COMMAND_CENTER/V2_REPAIR/ASTRA/SOLANA/
INTELLIGENCE gates on that exact SHA, open/update PR into
`integration/continuity-steward-closure-20260906`, report what is broken or
misbehaving with SHA + repro steps + evidence tier.
