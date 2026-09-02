# FABLE 5.1 — FINAL RETEST (post-remediation)

Independent re-derivation on the final HEAD: every finding re-attacked with the original PoC
(or the RED test encoding it) plus adversarial variants; nothing marked FIXED from mocks alone
where a runtime effect exists (real subprocesses for terminal, real SQLite rows, real files,
real ASGI apps, real asyncio servers; docker/GPU honestly NOT_TESTED_ON_THIS_HOST).

## Finding table

| ID | Sev | Disposition | Where | Proof |
|---|---|---|---|---|
| F-001 | HIGH | FIXED | bossman/toolkit/files.py | poc_search_glob.py blocked; test_tools.py |
| F-002 | MED | FIXED | bossman/toolkit/files.py | poc_sibling_probe.py blocked; test_tools.py |
| F-003 | MED | FIXED | bossman/toolkit/media.py | test_tools.py |
| F-004 | MED | FIXED | bossman/toolkit/net.py | test_secrem_f004_http_ssrf.py |
| F-005 | MED | FIXED | bossman/projects/runner.py | test_secrem_f005_projects.py |
| F-006 | MED | FIXED (core + cc) | bossman/context.py; bcc/features/tools_facts.py | test_secrem_f006_f007_untrusted.py; test_secrem_facts_boundary.py |
| F-007 | LOW | FIXED | bossman/runner.py | test_secrem_f006_f007_untrusted.py |
| F-008 | MED | FIXED | bossman/gateway/{app,client,main}.py, llm.py | test_secrem_f008_gateway_failclosed.py |
| F-009 | HIGH | FIXED (host-path authz, all modes) + NOT_TESTED_ON_THIS_HOST (container mount runtime) | bcc/features/tools_terminal.py, bcc/v2/terminal_control.py, bcc/features/terminal.py | test_secrem_f009_terminal.py, test_secrem_f015_self_assert.py |
| F-010 | MED | FIXED | bcc/v2/browser_control.py | test_secrem_browser_policy.py |
| F-011 | MED | FIXED (terminal, browser, opencode) | tools_terminal.py, tools_browser.py, tools_opencode.py | test_secrem_f009_terminal.py, test_secrem_f011_session_ownership.py |
| F-012 | MED | FIXED | bcc/v2/verification.py, bcc/features/review_gate.py | poc_cc_verify_auth_v2.py blocked; test_secrem_f012_verification.py |
| F-013 | MED | FIXED | bcc/tools.py, bcc/engine.py | test_secrem_f013_approval_identity.py |
| F-014 | MED | FIXED | bcc/features/tools_mcp.py, bcc/v2/mcp_hub.py, bcc/features/skills.py | test_secrem_mcp_boundary.py |
| F-015 | LOW | FIXED (approval-by-record); single-token HTTP authority ACCEPTED_RISK_REQUIRES_OWNER | bcc/approvals.py, bcc/features/{terminal,browser}.py | test_secrem_f015_self_assert.py |
| F-016 | MED | FIXED (gateway + cc router/forks) | gateway/app.py, bcc/features/{router,forks}.py, bcc/v2/model_router.py | test_secrem_router_failclosed.py |
| F-017 | LOW | FIXED | bcc/discovery.py, bcc/features/task_exchange.py | test_secrem_discovery.py |
| F-018 | INFO | FIXED (wired: fileintel/analysis, permissions deny-list, code_index containment; dispositioned: context_os NOT WIRED, capabilities/secret broker GATED_NON_PROTECTIVE; mask_enc removed) | toolkit/__init__.py, bcc/v2/{code_index,permissions}.py, bcc/context_os | test_secrem_f018_wiring.py (core + cc) |
| BUG-004 | — | FIXED (Linux); Windows/RunPod re-run pending | bossman/db.py | test_stage13_auth_redteam.py |
| BUG-005 | — | BOUNDED/FIXED | bcc/discovery.py | test_secrem_discovery.py |

"DOCUMENTED" is not used as a closure state. Residual risks are listed explicitly in
FINAL_ATTACK_MATRIX.md and FINAL_SECURITY_GATES.json.

## Regression on final HEAD

- bossman-core: 1360 passed, 5 skipped, 0 failed
- command-center: 718 passed, 3 skipped, 0 failed
- repo root (learning, preflight, sampler, A/B portability): 35 passed, 0 failed
- compileall: OK; secret scan: PASS (synthetic canaries marked); Fable PoCs: 3 blocked, 2 Windows-only NOT_TESTED_ON_THIS_HOST, cc PoC v2 blocked.

## Contract changes owners must know

1. `/api/review/enable` without `evidence` no longer completes tasks automatically (UNVERIFIED →
   human escalation). Provide `evidence: [{kind, target, expect}]`.
2. Gateway: cloud requires `x-bossman-cloud-allowed: 1`; direct clients that omitted it are now
   local-only.
3. Browser: private/loopback targets need `BCC_BROWSER_ALLOW_PRIVATE=1` (local dev).
4. MCP stdio servers must be launched by an allowlisted binary (`BCC_MCP_COMMAND_ALLOWLIST`).
5. `/api/terminal/run` and `/api/browser/sessions/{id}/act` require `approval_id` of an approved
   record; `approved: true` is rejected.
6. `http` tool: private/metadata targets denied unless `BOSSMAN_HTTP_ALLOW_HOSTS` /
   `BOSSMAN_HTTP_ALLOW_PRIVATE`; confirmation by default.
