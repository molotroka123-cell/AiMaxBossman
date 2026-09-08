# Original findings → fix → test → status

Sources: `ACCEPTANCE_FINAL_20260908.md`, `AUDIT_CLOUD_QA_20260908.md`,
`ui-walkthrough.json`, `tasks-trace.jsonl`, and the owner session
`6cbb17ce84db` (5 907 records, 38 dead clicks, 71 refusals, 33 errors).

Verified against the code at `72bae3e`, not against the reports. A row says
PROVEN only where a test or a direct probe demonstrates it; everything else is
an acceptance-test item.

## Proven fixed — code and test evidence

| # | Original finding | Where it is fixed | Evidence |
|---|---|---|---|
| 1 | **P0 review/escalation deadlock**, reproduced 4× — `waiting_approval` with an empty approvals queue, only `/stop` escaped | `bcc/review_escalation.py`; `review_gate._tick` now settles rejections and runs `finalize_override` *before* renaming; `reconcile()` sweeps every 10s unconditionally | `test_p0_review_deadlock.py` (15 tests); measured `review_deadlock_rate = 0.0` from 4 reproduced |
| 2 | **P1 phantom obligation from a URL** — a hostname matched the filename regex, so `https://example.com` became an unsatisfiable `file:example.com` | `action_contract._urlish_spans()` | `test_action_contract.py`; direct probe of `bossman-core`'s extractor confirms `example.com` no longer yields a `FileEffect` |
| 3 | **P1 verdict "не соответствует" with `expect={exists:true}`** | the Command Center contract now builds only `expect={"exists": True}` — it has no content comparison to get wrong | `test_action_contract.py` |
| 4 | **P1 approval storm** — ~121 confirmations across 3 tasks | `bcc/approval_scope.py`: dedup, refusal suppression, owner-granted scoped leases, per-task budget | `test_approval_scope.py` (40); measured 2 and 1 approvals against the corpus's 60 |
| 5 | **P1 token runaway** — 1 282 044 tokens and $4.04 for a two-line doc fix | `bcc/mission_budget.py`: token/cost ceilings, identical-call guard, stalled-context guard | `test_mission_budget.py` (17); synthetic contract run 1 840 / 920 — scripted token constants, not measured model usage; 1 295 189 is the corpus's historical figure, not an A/B baseline |
| 6 | **Provider wizard dead click** — `POST /api/providers` → 403 in 0.0 ms (session log, twice) | a CSRF 403 is now classified as an auth failure and sends the owner to the login form instead of a dead button | `test_browser_navigation_ui.py::test_lost_csrf_token_requires_login_instead_of_dead_403`, which cites this exact journal |
| 7 | **Browser session 404 / act 403** | policy 403 keeps the session; a real 401 requires login | `test_browser_navigation_ui.py::test_policy_403_keeps_session_but_401_requires_login` |
| 8 | **Reconnect** | countdown banner and restored toast | `test_ux2_reconnect.py::test_restart_shows_countdown_banner_and_restored_toast` |
| 9 | **GLM streaming — 0 chunks** (B4) | `bcc/streaming.py`, one canonical SSE parser; `DEGRADED` is not `ok` | `test_streaming_contract.py` (30) |
| 10 | **Silent / free model counted healthy** (B5) | `bcc/model_health.py`: answer-based classification, `SILENT`, `ProviderError(kind="empty_response")` | `test_model_health.py` (47) |
| 11 | **Model status did not detect an expired key** (P2) | `UNAUTHORIZED` is a first-class health status | `test_model_health.py` |
| 12 | **Apps start/stop → 409 on all nine** (B3) | persistent owner policy, effective immediately, no env ritual; `403 APPS_CONTROL_DISABLED` split from `409` | `test_apps_control.py` + `test_apps_control_child_encoding.py` (42) |
| 13 | **Video Studio thumbnail/waveform → bare 409** | the real reason is returned: `derivative_not_prepared`, with what to do; storage details stay in the server log | `test_video_descriptor_boundary.py`, `test_video_studio_read_verification.py` |
| 14 | **Web Designer `ai-edit` → 502** | an unreachable model is reported as "модель недоступна" rather than a silent no-op | `test_web_designer.py`, `test_web_designer_p2_findings.py` |
| 15 | **Cloud agents cannot test the local UI** (B2) | signed QA relay: HMAC jobs, single-use nonces, TTL, capability allowlist, sanitized evidence, kill switch | `test_qa_relay.py` (47); 3-system QA 4/4 systems, **0 manual interventions**, all four negative controls held |
| 16 | **Trading Lab → 500 `ModuleNotFoundError: bossman_v3`** | lazy import; a missing core returns an honest `UNWIRED` badge instead of crashing | code verified; **no dedicated regression test** → also an acceptance item |
| 17 | **Invented resource state** (128 GB with nothing measured) | admission fails closed when memory is not measured | `test_v6_resources_unmeasured.py` |
| 18 | **Duplicate "Пульт" in the sidebar** | the duplicate is hidden from the menu while the route stays reachable | `test_ux_navigation_shape.py::test_no_two_menu_entries_carry_the_same_name` |
| 19 | **UI dead clicks / console errors / network errors** | — | the 34-tab walkthrough recorded `broken: 0`, `console_errors_total: 0`, `network_4xx_5xx_total: 0`, `visual_bug: 0` |

## Requires live acceptance — not deterministically provable here

| # | Item | Why it cannot be closed from the repository |
|---|---|---|
| A | **OpenRouter connect** (502 ×6, 503, 400 in the session) | needs a real key against the real service; contract tests exist (`test_feat_openrouter*.py`, 5 files) but the failure was upstream |
| B | **GLM streaming, live** | the parser is proven; whether the provider streams is the provider's behaviour |
| C | **Free/silent model fallback, live** | classification is proven; the specific free route's behaviour is not ours |
| D | **Blocked mission state** ("у задачи не выбран агент") | admission paths are covered (`test_executor_admission.py`), but the original was a live configuration state |
| E | **Trading Lab UNWIRED badge** | fixed in code, no regression test — confirm on screen |
| F | **Video Studio / Web Designer end to end** | real media, real encoder, real model |
| G | **Local model** | no local runtime in this environment |
| H | **`chromium probe=false`** blocking the browser tool in the acceptance run | environment provisioning, not code |

## Open and deliberately untouched (freeze policy: no P2/cosmetic work before acceptance)

| # | Item | Severity | Note |
|---|---|---|---|
| i | No liveness URL — `/health`, `/healthz`, `/api/health` all 404 | P2 | health is real and rendered on **Система** (and in `/api/system`'s `health` block); adding an endpoint is a new feature |
| ii | A goal containing a template placeholder (`Отчёт <current timestamp>`) yields an unsatisfiable content obligation on the desktop-operator path | P2 | probed directly: `contains='Отчёт <current timestamp>'` is still extracted. The Command Center path is unaffected (no content comparison), and the deadlock that made this fatal is fixed, so the failure is now bounded and explained rather than a hang |
| iii | `bcc.desktop` stops the server when the browser window closes | by design | it only stops a server **it started**; running `python -m bcc.app` separately avoids it, which is step 1 of the acceptance plan |
| iv | command-bar / parse — persistent 409 (suspected stuck lock) | P2 | not reproduced here; acceptance item |
| v | Sandbox writes the file twice (real path + mirror) | P2 | not touched |
| vi | Web Designer "Открыть проект" creates a new project | P3 | label does not match the action |
| vii | Sticky validation toast survives a tab change; router form slow to render | P3 | from the walkthrough |
