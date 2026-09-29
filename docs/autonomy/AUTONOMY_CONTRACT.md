# Bossman autonomy - interface contract (shared by both implementation lines)

Source plan: owner's `BOSSMAN_JEV_TYPESCRIPT_AUTONOMY_ONE_RUN_PLAN.md` (desktop) + `docs/constitution/BOSSMAN_CONSTITUTION.md`.
Package: `command-center/bcc/autonomy/`. REUSE, do not rebuild: `bcc.jev` (Jev: OpenRouter System One reasoning, web
search via the existing web helpers), `bcc.rave.connectors` (official Claude CLI / Codex CLI, subscription login,
child env without secrets), `bcc.rave.workspace` (isolated clones, tamper fingerprint), `bcc.pit.tasks` (state record
patterns, idempotency keys), `bcc.pit.j2.quality_lab` (metrics/rubric), approvals and evidence helpers already in Bossman.

## Line A (control plane) owns
`types.py`, `schemas.py`, `constitution.py`, `goals.py`, `lease.py`, `hands.py`, `policy.py`, `journal.py`,
`staging.py`, `metrics_gate.py`, `manifest.py`, `cli.py`, `schemas/autonomy/*.schema.json`,
`features/autonomy.py` (API), `ui/pages/autonomy.js` (release panel), `terminal_cli` hook for `bossman autonomy ...`.

## Line B (workers, review, skills, cycle) owns
`workers.py`, `review.py`, `skills.py`, `planner.py`, `cycle.py`, `identity_task.py` (first real task).

## Shared types (Line A defines them in `bcc/autonomy/types.py` exactly like this; Line B codes against them)

```python
GoalState = Literal["PROPOSED", "PLANNED", "BUILDING", "TESTING", "CLAUDE_REVIEW", "CODEX_REVIEW", "STAGING",
                    "USER_APPROVAL", "DEPLOYED", "MONITORING", "COMPLETE", "ROLLED_BACK", "BLOCKED"]
RiskTier = Literal["docs_tests", "prompts_models", "memory_keys_telegram_services", "critical_runtime"]

@dataclass(frozen=True)
class Budget:            # a goal without a budget is refused
    max_minutes: int; max_agent_turns: int; max_cost_usd: float = 0.0

@dataclass(frozen=True)
class Goal:
    goal_id: str                 # "JEFF-0042"
    problem: str
    desired_result: str
    constraints: tuple[str, ...]
    acceptance_tests: tuple[str, ...]   # each must be an executable check reference or a measurable statement
    budget: Budget
    risk_tier: RiskTier
    target_metric: str           # e.g. "identity_redteam.leaks"
    protected_metrics: tuple[str, ...]

@dataclass(frozen=True)
class HandRequest:               # the structured hand protocol from the plan
    goal_id: str; requested_by: Literal["claude", "codex", "jev", "jeff"]; action: str; target: str
    arguments: dict; expected_evidence: tuple[str, ...]; risk_class: Literal["low", "medium", "high"]
    timeout_s: int; rollback: str   # owner update 29.09: every action carries a timeout and a rollback path

@dataclass(frozen=True)
class HandResult:
    request_hash: str; ok: bool; exit_code: int | None; started_at: str; finished_at: str
    artifacts: dict[str, str]    # name -> sha256
    refused_reason: str = ""

@dataclass(frozen=True)
class Review:
    goal_id: str; reviewer: Literal["claude", "codex"]; sha: str; diff_sha256: str
    verdict: Literal["APPROVE", "REQUEST_CHANGES", "REJECT"]; notes: str
```

## State machine (owner update 29.09, enforced by `goals.GoalStore`)

```text
PROPOSED -> PLANNED -> BUILDING -> TESTING -> CLAUDE_REVIEW -> CODEX_REVIEW -> STAGING -> USER_APPROVAL
         -> DEPLOYED -> MONITORING -> COMPLETE          (MONITORING/DEPLOYED -> ROLLED_BACK -> PLANNED)
revision (TESTING/CLAUDE_REVIEW/CODEX_REVIEW/STAGING/USER_APPROVAL -> BUILDING) invalidates both approvals
any state -> BLOCKED: rejection, timeout, disagreement, changed SHA, missing evidence, ambiguity
BLOCKED -> the state it was blocked from (resume) or PLANNED
```

Guards: `CLAUDE_REVIEW -> CODEX_REVIEW` needs Claude's APPROVE, `CODEX_REVIEW -> STAGING` needs both APPROVEs,
all bound to the current candidate SHA + diff hash; `STAGING -> USER_APPROVAL` needs a passed staging report for
that SHA; `USER_APPROVAL -> DEPLOYED` needs the user's Apply decision for that SHA and the owner's confirmation.
A new candidate SHA/diff invalidates approvals and staging. A reviewer never approves its own candidate.

## Wire schemas

`schemas/autonomy/{task,action,review,result}.schema.json` (Goal, HandRequest, Review, HandResult);
`bcc.autonomy.schemas` validates them without a jsonschema dependency (`goal_from_json`, `hand_request_from_json`,
`review_from_json`, `hand_result_from_json`, `to_json`).

## Line A functions Line B may call
- `constitution.verify() -> ConstitutionStatus` (ok, sha, pinned_sha, reason)
- `goals.GoalStore(root).create(goal) / get(id) / transition(id, new_state, evidence) / list(...)`; illegal
  transitions raise; every transition goes to the journal.
- `lease.EngineeringLease(root).acquire(goal_id, holder, ttl_s) -> LeaseToken`, `.release(token)`, `.current()`;
  exactly one writer at a time across processes; stale leases expire; orphan process groups are killed on expiry.
- `hands.HandBroker(policy, journal, executor).execute(req: HandRequest) -> HandResult` - the ONLY privileged path.
- `policy.Policy(...).check(req, goal, level) -> Decision(allowed, reason, needs_user)`.
- `journal.Journal(root).append(kind, payload) -> entry_hash` (hash-chained, append-only, secret-redacted), `.verify()`.
- `staging.StagingRunner(...).run(sha, checks) -> StagingReport` (separate port, temp data dir, never the owner data).
- `metrics_gate.decide(before, after, target, protected, thresholds) -> GateVerdict`.
- `manifest.build_manifest(journal, artifacts, required) -> dict` (freeze manifest: every required artifact with
  its sha256, linked to the journal head hash and appended to the journal); `manifest.verify_manifest(...)`.
- `schemas.validate(name, obj)` for `task`, `action`, `review`, `result`.

## Rules for both lines
Fakes only in tests (no real Claude/Codex/OpenRouter/Ollama/Telegram/network/participant data); deterministic tests;
no `asyncio.shield` under `command-center/bcc` (use `bcc.single_flight.await_shared`); LF endings, no trailing blank
line at EOF; no secret-looking literals; the autonomy loop never merges to a protected branch, never pushes release,
and stops at the user gate.
