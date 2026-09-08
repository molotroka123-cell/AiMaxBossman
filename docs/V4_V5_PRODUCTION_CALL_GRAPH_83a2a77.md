# V4/V5 Production Call-Graph Audit — source head `83a2a77`

SOURCE_HEAD: `83a2a77be66a7fe80d201279b9b8c92e5ffd8926`
Branch: `claude/v4-v5-freeze-p0-gates-l56exm` (re-based onto the PR #37 source head)

Independent read-only audit of the production call graph, produced to prove whether the
V4/V5 gates are actually reachable from production rather than only unit-tested.
Claims marked **[VERIFIED]** were obtained by reading the cited lines. **[INFERRED]**
marks reasoning not read directly off a line.

## A) Broad activation

### A.0 `objective_canary` has zero production importers — CONFIRMED

**[VERIFIED]** Repo-wide grep for `objective_canary` / `authorize_broad_activation` returns
one definition and four consumers, all tests:

- `bossman_shared/objective_canary.py:368` — `authorize_broad_activation` (definition)
- `tests/test_v5_freeze_regressions.py:5`
- `tests/test_v5_canary_rollback.py:25-27,146,157,284`
- `tests/test_v5_canary_attested_evidence.py:23-26,53,287,305,315`

No file under `bossman-core/`, `command-center/`, `apps/`, `bossman-infra/`, `tools/`,
`scripts/`, `learning/` imports it. **P0-1 is real and unmitigated at this head.**

**Additional finding, worse than the scorecard states:** `ObjectiveStore` carries a
complete canary run schema that nothing writes.

- `bossman_shared/objective_store.py:865` `open_canary_run`
- `bossman_shared/objective_store.py:911` `record_canary_report`
- `bossman_shared/objective_store.py:947` `close_canary_run`
- `bossman_shared/objective_store.py:896` `canary_run`, `:937` `canary_reports`

**[VERIFIED]** grep for these five names across the entire repo (tests included) returns
**only** their definitions. The durable canary tables are dead schema: no production caller
and no test caller. Even the persistence half of the canary door is unexercised.

**[VERIFIED]** `bossman_shared/objective_promotion.py` and `objective_improvement.py` are
likewise production-dead. The entire V5 promotion/canary decision stack is a test-only island.

### A.1 The live broad-activation path: Command Center skill promotion

The one place in the tree where a candidate is promoted to the whole cohort by production
code with no owner in the loop.

| # | file:line | function | State flipped | Consults `authorize_broad_activation`? |
|---|---|---|---|---|
| A1 | `command-center/bcc/v2/skill_evaluation.py:222-228` | `_apply_promotion` | `UPDATE skills SET current_version_id = <candidate>` — every future resolution of that skill, for every task and caller, gets the candidate. No cohort field exists in `skills` / `skill_versions` (`command-center/bcc/db.py:237`). This IS whole-fleet activation. Emits `skill.version.promoted`. | **No.** Zero canary import in the module. |
| A2 | `command-center/bcc/v2/skill_evaluation.py:202-206` (`refresh`) | automatic verdict -> calls A1 | Same, decided by `decide()` at `:96-120` purely on `success_rate` delta >= `IMPROVE_DELTA` over `MIN_RUNS`. `decided_by="runtime"`. | **No.** |
| A3 | `command-center/bcc/v2/skill_evaluation.py:246-250` (`apply_human_decision`) | owner approves a `HUMAN_REVIEW` -> calls A1 | Same. This one at least has a human. | **No.** |

**Full production reachability of A2 — [VERIFIED] end to end:**
`command-center/bcc/features/skills.py:816-822` registers an engine hook
(`svc.engine.add_hook("after_run", after_run)`) whose body calls `_after_skill_run` at `:820`.
`_after_skill_run` at `:374-396` calls
`await evaluation.refresh_for_version(svc, int(m["skill_version_id"]))` at `:395`.
`refresh_for_version` (`skill_evaluation.py:267-275`) calls `refresh` on every `collecting`
evaluation, and `refresh` calls `_apply_promotion`.

**Every completed skill task can autonomously flip `current_version_id` for the whole fleet.**
No canary, no cohort, no attested evidence, no rollback plan recorded.

Additional HTTP entry points into the same door, all **[VERIFIED]**:

- `command-center/bcc/features/skills.py:478-499` `POST /skill-evaluations` — opens *and
  immediately refreshes*, so one API call can trigger A2 in a single request.
- `command-center/bcc/features/skills.py:502-507` `POST /skill-evaluations/{id}/refresh` — direct A2.
- `command-center/bcc/features/skills.py:510-521` `POST /skill-evaluations/{id}/decide` — A3.

### A.2 BYPASS route — the strongest finding in section A

**[VERIFIED]** `command-center/bcc/features/skills.py:270-297`, `_persist_skill_version`:

```
vid = int((await s.execute(sa.insert(skill_versions_t).values(...)))...)
await s.execute(sa.update(skills_t).where(skills_t.c.id == sid).values(
    current_version_id=vid))                       # skills.py:295-296
```

Merely *registering a new skill version fingerprint* sets `skills.current_version_id` to it
unconditionally. This reaches **the exact terminal state `_apply_promotion` guards**
(`skill_evaluation.py:226`) while:

- performing zero measurement (`MIN_RUNS` never consulted);
- skipping `widened_capabilities` entirely (`skill_evaluation.py:88-93`) — so the promise
  advertised at `skills.py:475-476` and in the module docstring `skill_evaluation.py:19`
  **does not hold** on this path: a version declaring new `required_tools` / `permissions`
  becomes current on registration;
- never emitting `skill.version.promoted`, so the promotion is invisible on the bus;
- never writing a `skill_evaluations` row, so the audit tab shows nothing.

**[INFERRED]** whether an untrusted caller can reach it depends on who may register a skill
contract; the callers of `_persist_skill_version` were not traced to an HTTP route. The
state-flip equivalence itself is verified.

### A.3 Other promotion functions — all production-dead or non-fleet

**[VERIFIED]** each exists but reaches no fleet-wide production state at this head:

| file:line | function | State | Prod caller? |
|---|---|---|---|
| `bossman-core/bossman/learning_guard/promotion.py:142-157` | `promote` | `PromotionStage.OWNER_PROMOTED` in memory; requires `owner_approved`, `stage >= VERIFIED`, `security_proven` | Only `autonomy_trainer.py:26` |
| `bossman-core/bossman/learning_guard/autonomy_trainer.py:206-268` | `promote_candidate` | `status="PROMOTED"` on a dataclass | Only `apprentice/skills.py:293` |
| `bossman-core/bossman/apprentice/skills.py:287-301` | `SkillPromoter.promote` | `skill_state="READY"` persisted via `self.memory.store_skill` at `:300` — a real durable write | **No production caller.** Tests only. Gated behind `BOSSMAN_SKILL_PROMOTION` (`skills.py:268`, `apprentice/flags.py:12`). Consults no canary. |
| `bossman-core/bossman_v3/skill_factory/factory.py:66-82` | `promote` | `SkillStage.PRODUCTION` on a candidate object | None found |
| `bossman-core/bossman/cybersec/learning.py:69-79` | `promote` | `Stage.PROMOTED`, requires `owner_approved` | None found |
| `bossman-core/bossman/context_engine/memory.py:149-156` | `promote` | `MemoryStatus.ACTIVE` in sqlite — durable, but one memory row, not a revision rollout | `bossman-core/bossman/benchmark/sandbox_cases/context.py:184` (benchmark harness) |
| `apps/social-farm/src/social_farm/browser/capabilities.py:168-193` | `promote` | `VERIFIED_BROWSER` in an in-process dict | **No callers at all** |

### A.4 Objective revisions — the surface the canary was written for

**[VERIFIED]** `ObjectiveStore.revise` (`bossman_shared/objective_store.py:410-460`)
atomically swaps `spec_json`/`spec_digest`/`revision` and resets `condition='UNKNOWN'`.
No cohort concept in the write. Callers are tests only:
`command-center/tests/test_objectives_workspace.py:221`, `tests/test_v5_admission.py:246`,
`tests/test_v5_golden_missions.py:487`, `tests/test_v5_objective_store.py:184,344,358,370,383,393,395`.

**[VERIFIED]** `command-center/bcc/features/objectives.py` — the only production HTTP surface
over `ObjectiveStore` — exposes **no** revise route. Routes are read-only plus
`POST /objectives/{id}/lifecycle` (`:293-320`) and `POST /objectives/{id}/enrollment`
(`:322-339`). The file ends at `:340` with `# tick отсутствует намеренно` and sets
`STANDING_AUTONOMY_ENABLED = False` at `:41`.

**Conclusion for A:** there is currently no production path that rolls a *revision* to a fleet
at all, so `authorize_broad_activation` has nothing to be wired into on the objective side
without also building the rollout. The one live broad-activation path is the Command Center
skill promoter (A1-A3), and it has a registration-time bypass (A.2).

## B) `ObjectiveStore.set_condition` (`bossman_shared/objective_store.py:513`)

Store-level gate, **[VERIFIED]** at `:522-523`:

```
if condition == "SATISFIED" and not (type(evidence_ref) is str and evidence_ref.strip()):
    raise ObjectiveStoreError("SATISFIED requires a verified evidence reference")
```

A non-empty-string check only. It never resolves the ref. P0-2 confirmed exactly as stated
in `docs/v5/V5_RELEASE_SCORECARD.md:59`.

### Production callers — exactly two, only one can write SATISFIED

**B1. `bossman_shared/objective_reconcile.py:195` — inside `_write`, the only production
SATISFIED writer.** **[VERIFIED]** evidence provenance here is *better* than the store gate:

- `:249` — `refs = (str(evidence["evidence_ref"]),) if bound and condition != "UNKNOWN" else ()`
- `bound` is computed at `:228-231` and requires `verified`
  (`_evidence.verify_signed(evidence, key=evidence_key)`, `:225-227`) **and** `_bindings_ok`
  (`:146-166`), which pins `objective_id`, `objective_digest`, `objective_revision`,
  `mission_id`, `reservation_id`, and the exact tuple of `observation_digests`.
- `:194` — `ref = refs[0] if refs else carried`; on UNKNOWN paths it carries the previously
  verified ref forward rather than inventing one.

So the string handed to `set_condition` for SATISFIED **is** extracted from a
cryptographically verified, objective/revision/mission/reservation/observation-bound record.
It is not model prose. **[INFERRED]** it is still not proven to be a *resolvable durable
record id* — `_bindings_ok:163` only requires `_nonempty(record["evidence_ref"])`; the field
travels inside the signed envelope but nothing dereferences it against a store. The ref is
*attested* but not *resolved*.

**Decisive caveat: `reconcile_after_mission` has ZERO production callers. [VERIFIED]** —
grep outside the module returns only `bossman_shared/objective_recovery.py:22` (a docstring
mention) and `tests/test_v5_recovery.py`. **No production code path can currently write
SATISFIED at all.**

**B2. `bossman_shared/objective_recovery.py:236` — writes `"UNKNOWN"` only.**
**[VERIFIED]** `:236-238` passes `evidence_ref=state.last_verified_evidence_ref`, read back
out of the durable row, not a fresh claim. Cannot set SATISFIED. `objective_recovery.recover`
also has no production caller.

### Test call sites passing SATISFIED — fixtures needing real bound evidence

**[VERIFIED]**, complete list:

| file:line | `evidence_ref` passed |
|---|---|
| `tests/test_v5_objective_store.py:123` | `"ev-1"` — invented literal |
| `tests/test_v5_objective_store.py:314` | `"ev-1"` — invented literal |
| `tests/test_v5_objective_store.py:356` | `"ev-1"` — invented literal |
| `tests/test_v5_objective_store.py:460` | loop var `evidence` (parametrized strings) |
| `tests/test_v5_objective_store.py:477` | `"ev-1"` |
| `tests/test_v5_canary_rollback.py:215` | `"ev-verified-1"` — invented literal |
| `command-center/tests/test_objectives_workspace.py:174` | `"ev-1"` — invented literal |
| `tests/test_v5_golden_missions.py:551` | `None` — negative test, expects refusal |
| `tests/test_v5_golden_missions.py:554` | `"   "` — negative test, expects refusal |

Non-SATISFIED call sites for completeness: `tests/test_v5_objective_store.py:190,467,632,664`.

When the resolver check lands at `objective_store.py:522`, the seven positive fixtures break
unless each is rebuilt around a real signed record produced through
`objective_reconcile.bind_evidence` (`bossman_shared/objective_reconcile.py:118-135`) — the
existing constructor for a properly bound record, and the natural fixture helper.

## C) AT-01 obligations

### Entry points

- **Extraction:** `bossman-core/bossman/computer_operator/obligations.py:105`
  `extract_obligations(goal)`. Returns `FileEffect`s (`:135-136`), else a single `ScreenEffect`
  (`:140-142`), else `(UnknownEffect(...),)` (`:143`). The module is correct and fail-closed by
  design — `unsatisfied` at `:214-218` unconditionally reports every `UnknownEffect` as missing.
- **Verification:** `obligations.py:202` `unsatisfied(...)`, `:242` `snapshot(...)`, `:165` `file_probe(...)`.
- **Wiring:** `manager.py:6` imports them; `:136-137` binds `self.obligations_of` /
  `self.obligation_probe`; `:704-707` takes the pre-attempt snapshot in `_bind_attempt`.
- **Decision:** `manager.py:633` `_completion_blocked`, called from the `ActionKind.COMPLETE`
  branch of the run loop at `:267-314` (verdict at `:286`, verifier at `:288`, task set
  `COMPLETED` at `:314`).
- **Production construction:** `bossman-core/bossman/computer_operator/subsystem.py:198-215`
  `build_manager`, which **does** pass `obligation_probe=file_probe(Path.home())` at `:215`,
  and `MANAGER = build_manager()` at `:218`. `wiring.py:85-93 make_manager` does **not** pass a
  probe, but defaults to `FakeAdapter()` at `:89` and is used only by tests.

### C1. Untypable obligation NOT failing closed — `manager.py:655-660`

```
if obligations and all(isinstance(e, UnknownEffect) for e in obligations):
    obligations = ()                                   # manager.py:660
```

**[VERIFIED]** The precise defect. `extract_obligations` deliberately returns `UnknownEffect`
as an *unclosable* obligation (`obligations.py:77-90`), and `unsatisfied` honours that at
`:214-218`. The manager then throws it away. Control falls to `:665` (`if obligations:` — now
false) and drops to the weak rule at `:675-678`.

### C2. Verified-but-unrelated mutation discharges an unknown obligation — `manager.py:675-678`

```
for step in t.history:
    if (step.verified is True and step.finished_at is not None
            and step.action.kind not in _NON_EFFECT_KINDS):
        return None                                   # manager.py:678
```

**[VERIFIED]** `return None` means "completion allowed". The loop tests only that the step
verified, finished, and its `ActionKind` is not in `_NON_EFFECT_KINDS` (`manager.py:24-28`).
There is **no** relation between the step and the obligation — no path, no content, no target
window, no ordering. So for any goal `goal_requires_external_effect` (`manager.py:48-55`)
flags but from which no named effect could be extracted, *any* verified mutating action
anywhere in `t.history` discharges it. Exactly the failure `obligations.py:6-8` says was being
fixed, still live for the `UnknownEffect` class.

### C3. Second, undocumented fall-open — `manager.py:663-664`

```
if self.obligation_probe is None:
    obligations = tuple(e for e in obligations if not isinstance(e, FileEffect))
```

**[VERIFIED]** With no probe, *named file obligations* are silently discarded and the weak rule
at `:675-678` decides. Production (`subsystem.py:215`) does supply a probe, so this is not live
in `build_manager`; but any host constructing `ComputerOperatorManager` directly, or before the
probe is available, gets the AT-01 gate silently disabled with no event emitted.

### C4. Third fall-open — `manager.py:682-687`

```
def _obligations(self, t):
    try: return tuple(self.obligations_of(t.goal) or ())
    except Exception:
        return ()                                     # manager.py:687
```

**[VERIFIED]** Any exception in extraction yields "no named obligations", routing to the weak
rule. A crash in the extractor is treated as an absence of obligations — fail-open, and silent.

### What is correct

**[VERIFIED]** The FileEffect path is genuinely strong: three-part evidence at
`obligations.py:228-238` (exists now / content matches / differs from the pre-attempt
snapshot), the snapshot bound per attempt at `manager.py:704-707`, `ProbeResult(False)` on
`OSError` at `obligations.py:178-180` (refusal, not a guess), and the ScreenEffect path at
`obligations.py:219-227` requiring the screen to have actually changed. The hole is confined
to C1-C4.

## D) N4 / N5 / N6 / N8 — what real acceptance requires

### N4 — production scheduler

**Needed:** a running loop that, per tick, ranks admissible objectives via
`objective_fairness.rank()`, drives `AdmissionKernel`
(`bossman_shared/objective_admission.py:420`), and settles, so quota/cooldown/anti-oscillation
are exercised under contention rather than in a unit test.

**Where it lives: it does not exist in the repo. [VERIFIED]** — grep for `objective_fairness`
outside its own module returns no production importer (the two `rank(` hits,
`bossman-core/bossman/sandbox/models.py:98` and `bossman-core/bossman/projects/router.py:64`,
are unrelated methods). `AdmissionKernel` has no production importer either — only its
definition and the reference copy at
`handoffs/v5_fable_source/reference_implementation/admission.py:9`. The one production surface
over the store states the absence explicitly:
`command-center/bcc/features/objectives.py:340` (`# tick отсутствует намеренно`) and `:41`
`STANDING_AUTONOMY_ENABLED = False`.

### N5 — model retention -> measurement -> promotion caller

**Needed:** one production caller that runs the lane protocol against the owner's real model,
produces a paired-measurement report whose sha256 forms an
`intelligence_preservation/paired/<64hex>` ref (the pattern enforced at
`bossman_shared/objective_improvement.py:64`), feeds it to
`objective_promotion.measure`/`authorize` (`objective_promotion.py:194`, `:256`), and applies
the result.

**Where it lives:** the measurement runner exists — `tools/intelligence_preservation_run.py`
and `tools/intelligence_preservation_gate.py` (**[VERIFIED]** both present). **The caller that
joins runner -> `objective_promotion` -> an applied promotion does not exist. [VERIFIED]**
`objective_promotion` has zero production importers. The live promotion path
(`command-center/bcc/v2/skill_evaluation.py`) uses none of this machinery: it decides on raw
`success_rate` delta at `:110-115` and never measures retention.

### N6 — owner-visible real workspace

**Needed:** the objectives workspace opened in a real browser on the owner's machine, driving
the real backend against a real `v5_objectives.sqlite3`, with the Evidence and Revisions tabs
and Pause/Resume/Revoke actually clicked.

**Where it lives: it exists.** Backend `command-center/bcc/features/objectives.py` (routes at
`:265` revisions, `:293` lifecycle, `:322` enrollment; store bound to
`svc.settings.data_dir/v5_objectives.sqlite3` at `:44-48`); auto-registered via
`load_features()` (`command-center/bcc/api.py:34,115`). Frontend
`command-center/ui/pages/objectives.js`, registered at `command-center/ui/pages/index.js:35`.
**[VERIFIED]** all four. N6's gap is purely "not exercised by a human on the owner's box", not
"missing".

### N8 — production caller + restart + rollback

**Needed:** a production actor that (a) opens a canary run in the durable tables, (b) collects
attested member reports, (c) calls `authorize_broad_activation` with a real resolver,
(d) survives a process restart mid-run, and (e) executes the rollback order.

**Where it lives: none of the five exists in production. [VERIFIED]**
(a) `open_canary_run`/`record_canary_report`/`close_canary_run`
(`objective_store.py:865,911,947`) have **no callers anywhere, tests included**.
(c) `authorize_broad_activation` has no production caller.
(e) `objective_recovery.prepare_rollback` (`:257`) and `resume_point` (`:244`) have no
production caller. The revision-rollout surface the canary would gate does not exist either
(section A.4). N8 is repo-local in the strictest sense: the decision logic is well-tested, and
nothing in production can reach it.

## Summary of P0 status at `83a2a77`

- **P0-1: CONFIRMED and larger than stated.** Not only is `authorize_broad_activation`
  unimported by production — the durable canary tables (`objective_store.py:865-960`) have zero
  callers of any kind, and `objective_promotion`/`objective_improvement` are equally
  production-dead. Meanwhile the one live broad-activation path
  (`command-center/bcc/v2/skill_evaluation.py:222-228`) is fully autonomous via the engine
  `after_run` hook and has a registration-time bypass at
  `command-center/bcc/features/skills.py:295-296` that also defeats the "no automatic privilege
  widening" promise.
- **P0-2: CONFIRMED at the store (`objective_store.py:522-523`), but the exploitable surface is
  narrower than feared** — the only production SATISFIED writer (`objective_reconcile.py:195`)
  already derives its ref from a signature-verified, fully bound record, and it has no
  production caller at all. Seven test fixtures pass invented literals and will need real bound
  evidence.
- **P0-3: CONFIRMED, plus two additional undocumented fall-open branches**
  (`manager.py:663-664` no-probe, `manager.py:682-687` extractor-exception) beyond the known
  `UnknownEffect` drop at `manager.py:660`, all three converging on the unrelated-mutation rule
  at `manager.py:675-678`.
