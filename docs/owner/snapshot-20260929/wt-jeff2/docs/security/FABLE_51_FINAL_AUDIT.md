# FABLE 5.1 — FINAL SECURITY AUDIT REPORT

Independent adversarial red-team + minimal repair of the owner's own repository.
Prior conclusions were not trusted; every claim was re-derived from code and controlled PoCs.

```
AUDITED_SHA=bb944d47864e70c3b93f01382e94f22dd59aeab5
FINAL_SHA=74c28c3c0bfb67bb9fa41553ca5d0893730484cd (+ uncommitted Phase-2 fixes; see Fix Log)
REMOTE_SHA=74c28c3c0bfb67bb9fa41553ca5d0893730484cd  (== LOCAL; branch HEAD advanced 1 docs-only commit past target)

ATTACKS_ATTEMPTED=20 categories (+ auth + secret-in-URL side probes)
ATTACKS_BLOCKED=6 categories fully BLOCKED (auth, cache, learning-guard, recovery; cloud-egress blocked in planner path)
EXPLOITABLE_FOUND=6 reproduced (F-001,002,003,009,012 + junction/git-parent), plus PARTIAL trust-boundary gaps

CRITICAL=0
HIGH=2   (F-001 fs.search read-anywhere [FIXED]; F-009 terminal.run sandbox root-skip [OPEN, design])
MEDIUM=11 (F-002,003 [FIXED]; F-004,005,006,008,010,011,013,014,016 [OPEN])
LOW=4    (F-007,015,017 + scheduler/concurrency/DoS hardening [OPEN])
INFO=1   (F-018 dead/unwired security code)

FIXED=3   (F-001 HIGH, F-002 MED, F-003 MED — path confinement, with regression tests)
OPEN=15   (documented; design-level or policy-addition, not silently rewritten)

APPROVAL_BYPASS=PARTIAL       (runner approval well-bound & no TOCTOU; projects path + CC name-resolve/self-assert bypass it — F-005,013,015)
AUTH_BYPASS=NONE_FOUND        (Stage 6 device/scope fail-closed; revoked/unknown/missing all deny; no principal cache; re-query per request)
POLICY_BYPASS=YES             (path confinement F-001/002/003 FIXED; terminal sandbox F-009, browser allowlist F-010, cloud gating F-016 OPEN)
ARBITRARY_SHELL=PARTIAL       (argv discipline holds; no arbitrary host shell from default planner path; F-009 sandbox mounts arbitrary dir into container; projects cmd is templated shell)
PROMPT_INJECTION=PARTIAL      (EXTERNAL_DATA_HEADER present but gated on read/send rights; exec/write output unmarked — F-007)
INDIRECT_PROMPT_INJECTION=PARTIAL (retrieved->system unmarked F-006; self-authored facts unmarked; MCP descriptions verbatim F-014)
MCP_SECURITY=PARTIAL          (results marked untrusted; descriptions/schemas verbatim into catalogue; re-register-by-name swaps approved impl; arbitrary command owner-gated — F-013,014)
TOOL_SPOOFING=PARTIAL         (downstream trusts textual claims; no fresh-observation cross-check — F-012)
VERIFICATION_SPOOFING=YES     (review gate PASS on echoed criteria; LLM verdict = self-report startswith PASS — F-012, reproduced)
SECRET_LEAKAGE=PARTIAL        (strong redact in core logs/flight-recorder; gateway process lacks RedactionFilter; CC approval previews/events/tool_calls.result_preview unredacted)
MEMORY_POISONING=PARTIAL      (decision/failure memory not read back into prompts; retrieved + facts are the injection surface — F-006)
LEARNING_GUARD=BLOCKED        (auto-promote only no-widening + >=5 runs + delta>=0.10; widening=>owner approval; current_version_id unread at exec = inert)
CACHE_POISONING=BLOCKED       (NEVER_CACHE_KINDS fail-closed; only parsed_registry[mtime]/parsed_file cached; no tool-result/policy caching)
ROUTER_MANIPULATION=PARTIAL   (cloud default-allow when no meta; force_model_id override; local-mislabel — F-016)
CLOUD_EGRESS=BLOCKED_IN_PLANNER_PATH (never-agent blocked pre-network + gateway route-drop; header fail-open default + embeddings + is_cloud() prefix audit-hole are hardening — F-008)
FILE_SECURITY=FIXED_HIGH      (F-001/002/003 fixed; code_index symlink-index escape OPEN; sandbox/artifacts.py is the correct model)
BROWSER_SECURITY=PARTIAL      (no default URL allowlist F-010; cross-session hijack F-011; actor='human' self-assert F-015)
RECOVERY_ABUSE=BLOCKED        (replay guards: tool_calls outcome + (run_id,call_id) uniqueness; failures recorded honestly)
SCHEDULER_SECURITY=PARTIAL    (core scheduler inert; CC no overlap guard; perms re-checked per tool-call at exec)
CONCURRENCY_RACES=LOW         (CC scheduler overlap accumulation bounded by BCC_WORKERS)
RESOURCE_ABUSE=LOW            (discovery file-scan unbounded; MCP structured passthrough unbounded; /api/login no rate-limit/lockout)

BUG004_SECURITY_RELEVANCE=LOW (pytest-asyncio/asyncpg loop-scope infra; auth fail-closed; no cross-task/loop auth leak; no principal cache)
BUG005_SECURITY_RELEVANCE=LOW (network probes wait_for-bounded 2.5s/1.0s; silent attacker port cannot hang; only local file-scan unbounded, not remote)

DEAD_UNWIRED_SECURITY=YES     (bcc/v2/permissions.py deny-list; code_index._within; context_os/*; sandbox/secrets.py Secret Broker; capabilities.py; toolkit/analysis.py+fileintel.py never registered; secrets.mask_enc)

REGRESSION_CORE=1234 passed / 50 skipped / 0 failed (full bossman-core suite with fixes); security subset 158 passed / 3 skipped; tool tests 12 passed
REGRESSION_COMMAND_CENTER=NOT_MODIFIED_BY_FIXES (all fixes in bossman-core; CC behavior unchanged from its own baseline)
SECURITY_REGRESSION=0 in exercised subset (sandbox/browser/gateway/shell/stage12-13 redteam green)
```

## TOP_5_FINDINGS

1. **F-001 (HIGH, FIXED)** — `fs.search` `glob` argument bypassed workspace confinement, giving a
   compromised planner (default-granted `coder`/`analyst`) arbitrary file read outside the agent
   workspace. Reproduced and fixed with containment on every candidate path.
2. **F-009 (HIGH, OPEN)** — command-center `terminal.run` `mode="sandbox"` skips the allowed-roots check
   and bind-mounts an arbitrary model-supplied `cwd` read-write into the container, at `auto` effect
   (no approval). Design-level; recommend confining sandbox `cwd` to allowed roots.
3. **F-012 (MED, reproduced)** — verification/review gate returns PASS when the worker merely echoes the
   criteria string, and the LLM-reviewer path is a same-family self-report (`startswith("PASS")`).
   Verification is not grounded in fresh, independent external state.
4. **F-013/F-014 (MED)** — MCP boundary weaknesses: server-authored tool descriptions/schemas enter the
   model's tool catalogue verbatim (injection), and an approved tool call is re-resolved by name at
   resume, so a `refresh` that re-registers the handler executes a different implementation than the one
   approved (`args_hash` is computed but never verified).
5. **F-002/F-003 (MED, FIXED)** — `fs.*` root confinement used `str.startswith` (sibling-prefix escape)
   and `media.probe` had no path validation. Both fixed with proper containment / the existing
   `_path_arg_ok` barrier.

## TOP_5_ARCHITECTURAL_RISKS

1. **Verification is by self-report, not fresh independent observation.** The canonical invariant ends
   in "fresh observation → verification", but completion is accepted on model/tool textual claims
   (F-012, F-007). A fresh-evidence verifier gating "done" is the single highest-leverage hardening.
2. **command-center is a single-token, full-authority surface.** Every route == owner authority; there
   is no per-capability HTTP authorization, and several routes trust client-asserted approval flags
   (F-015). Fine for single-user localhost, fragile if ever bound beyond loopback (`BCC_HOST`).
3. **Text→authority boundary is applied inconsistently.** Tool read/send output is marked untrusted, but
   retrieved memory (injected as `system`), self-authored facts, exec/write output, and MCP tool
   descriptions are not (F-006/007/014). Any single untrusted ingestion path becomes instruction.
4. **MCP servers are trusted like first-party code** (arbitrary spawn command, verbatim descriptions,
   name-collision overwrite, re-register-by-name at resume) — F-013/014.
5. **Cloud-egress and routing policy default permissive/advisory** (client header fail-open, no global
   deny, `force_model_id`/local-mislabel overrides) — F-008/016. Blocked for `never`-agents in the
   planner path today, but the default direction is fail-open.

## UNRESOLVED_BLOCKERS (required before V2 freeze)

The reproduced HIGH containment break in the file layer is fixed. The following remain and are the
freeze blockers:

1. **F-009 (HIGH)** — command-center `terminal.run` sandbox root-skip + RW bind-mount without approval.
   Confine sandbox `cwd` to allowed roots (keep container isolation as defense-in-depth) and/or require
   approval for `system_admin`/host-dir mounts. Needs a docker host to validate.
2. **F-012 (MED, but invariant-level)** — make completion depend on fresh external evidence, not on
   `startswith("PASS")` / criteria-substring; at minimum, stop the deterministic echo-criteria PASS.
3. **F-013/F-014 (MED)** — verify `args_hash` (and tool-impl identity) at approval-resume; treat MCP
   tool descriptions/schemas as untrusted data, not instruction; add an MCP command allowlist.
4. **F-006/F-007 (MED/LOW)** — apply the untrusted-data marker uniformly to retrieved/system-injected
   and exec/write output.
5. **F-008/F-016 (MED)** — fail-closed cloud gating (deny by default; check embeddings; audit by resolved
   route, not alias prefix).

```
SAFE_FOR_V2_FREEZE=NO
```

Rationale: the file-layer HIGH is fixed and proven, but F-009 (HIGH, command-center sandbox root-skip)
and the verification-spoofing / approval-identity / untrusted-marker gaps above are unresolved and touch
core invariants. They are design/policy changes that must be decided and validated (F-009 on a docker
host) rather than patched blind. Once items 1–5 are addressed and retested with fresh external evidence,
re-evaluate the freeze.

---
Artifacts: `FABLE_51_RED_TEAM_INITIAL.md`, `FABLE_51_FINDINGS_INITIAL.json`, `FABLE_51_ATTACK_MATRIX.md`,
`FABLE_51_FIX_LOG.md`, `FABLE_51_RETEST.md`. PoCs: `.agents/redteam/*.py` (synthetic fixtures, canary
`BOSSMAN_TEST_SECRET_9F31A7`; no product code modified in Phase 1).
