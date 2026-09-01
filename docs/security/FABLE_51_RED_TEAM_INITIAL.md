# FABLE 5.1 — INDEPENDENT ADVERSARIAL RED-TEAM (PHASE 1, FIND-ONLY)

GITHUB_REPO=https://github.com/molotroka123-cell/AiMaxBossman
GITHUB_BRANCH=https://github.com/molotroka123-cell/AiMaxBossman/tree/claude/bossman-control-v03-43igbk
GITHUB_TARGET_COMMIT=https://github.com/molotroka123-cell/AiMaxBossman/commit/bb944d47864e70c3b93f01382e94f22dd59aeab5

## 0. Provenance / source of truth

- AUDITED_SHA (target from prompt) = bb944d47864e70c3b93f01382e94f22dd59aeab5 (exists locally: `git cat-file -t` → commit)
- LOCAL_SHA = 74c28c3c0bfb67bb9fa41553ca5d0893730484cd
- REMOTE_SHA (origin/claude/bossman-control-v03-43igbk) = 74c28c3c0bfb67bb9fa41553ca5d0893730484cd
- LOCAL == REMOTE ✓ (`git fetch` clean). Branch HEAD has advanced **1 commit** past the prompt target:
  `74c28c3 chore: remove scratch ssh-scripts from agents/tmp` — a docs/scratch cleanup only
  (`git diff --stat bb944d4..74c28c3` = 12 shell scripts deleted under `.agents/tmp/`, 0 product code).
  Audit performed against 74c28c3 (current REMOTE HEAD); no unknown local snapshot.
- Auditor: Fable 5.1, independent. Prior RunPod verdict (`docs/runpod/RUNPOD_FINAL_AUDIT.md`,
  "PARTIAL") was **not** taken as ground truth; all claims re-derived from code + controlled PoCs.

## 1. Scope & method

Owner-authorized assessment of the owner's own repo. Only authorized targets touched:
`bossman-core`, `command-center`, synthetic temp dirs, synthetic canary `BOSSMAN_TEST_SECRET_9F31A7`.
No external infra, no real secrets, no destructive payloads. All PoCs live under `.agents/redteam/`
(temporary fixtures; no product code modified in Phase 1).

Two independent trust-boundary maps were built (bossman-core, command-center) and then the highest-value
hypotheses were reproduced with deterministic Python PoCs against the real modules.

## 2. Canonical invariant under test

intent → typed action → policy/scopes → approval → executor → fresh observation → verification.

Critical assumption honored: **the LLM/planner is treated as compromised.** "The model refused" is not a
control. The question asked of every boundary: *if the planner emits a hostile typed action, does a trusted
enforcement layer still stop the unauthorized effect, verifiable by fresh external state?*

## 3. Headline results

- **Auth (Stage 6 device/scope) is genuinely fail-closed.** Revoked/unknown/missing tokens all deny;
  no principal cache; `authenticate()` re-queries the store each request. No auth bypass found.
- **Cloud egress for a `cloud_policy=never` agent is blocked in the planner path** (llm.py pre-network
  check + gateway route-drop). Residual issues are hardening, not planner-reachable bypass.
- **The strongest real defects are containment/enforcement gaps that a compromised planner reaches
  through granted tools**, chiefly:
  - `fs.search` `glob` argument is **not** path-confined → arbitrary file read outside the agent
    workspace (HIGH, reproduced, granted to default `coder` + `analyst`).
  - `fs.*` root check uses `str.startswith` → sibling-directory escape (MEDIUM, reproduced).
  - command-center `terminal.run` `mode="sandbox"` **skips** the allowed-roots check and bind-mounts an
    arbitrary `cwd` read-write into the container, at `auto` effect (HIGH by design, static-confirmed).
  - review/verification gate returns PASS when the worker **echoes the criteria string** (MEDIUM,
    reproduced) — verification spoofing.
- **Several text→authority gaps**: retrieved memory is injected as a `system` message with no
  untrusted marker; model-written facts are read back without the untrusted-data header that other
  tool output carries; hostile MCP tool descriptions/schemas enter the model's tool catalogue unfiltered.

## 4. Open-bug security relevance (from prompt §26)

- **BUG004_SECURITY_RELEVANCE = LOW.** The 3 auth-redteam failures are a pytest-asyncio/asyncpg
  event-loop-scope infra problem ("Future attached to a different loop"). The auth path itself is
  fail-closed: a loop error raises → the request errors out (no authorization granted). No principal
  is cached across loops/tasks; `DeviceService.authenticate` re-queries the store each call and denies
  on any mismatch/revocation (reproduced in `poc_cc_verify_auth.py`). Worst case is a liveness/DoS of
  auth under loop mismatch, not an authorization bypass or cross-task auth leak.
- **BUG005_SECURITY_RELEVANCE = LOW.** discovery's network probes are individually timeout-bounded
  (`PROBE_TIMEOUT=2.5s`, `PORT_TIMEOUT=1.0s`, both via `asyncio.wait_for`, writer closed in `finally`),
  so an attacker-controlled *silent* port cannot hang discovery — the hanging test is a Linux
  pytest-asyncio infra artifact. The one genuinely unbounded piece is the local filesystem scan
  (`_scan_files` via `asyncio.to_thread`, `rglob` with no timeout), reachable only through
  operator-set `BCC_MODELS_DIRS`, not remotely. No discovery DoS from an attacker port.

## 5. Verdict for freeze

**SAFE_FOR_V2_FREEZE = NO** at Phase-1 close, blocked by the reproduced HIGH containment breaks
(F-001 `fs.search` glob read-anywhere, and its MEDIUM sibling F-002/F-003). These are pure-logic,
default-reachable, and have a clear minimal root-cause fix; they are addressed in Phase 2
(`FABLE_51_FIX_LOG.md`). Remaining findings are documented for owner triage; the larger
architectural items (command-center single-token authority model, MCP command allowlist,
verification-by-fresh-observation) are design decisions, not silently rewritten.

Full evidence: `FABLE_51_FINDINGS_INITIAL.json`, `FABLE_51_ATTACK_MATRIX.md`.

INITIAL_FINDINGS_FROZEN=YES
