# Epoch 4 foundation integration evidence

Verdict: **V4_NOT_COMPLETE**. V5_NOT_COMPLETE. Threefold performance:
**NOT_MEASURED**. Human-level computer control: **NOT_MEASURED**.
Date: 2026-09-06. Production activation remains disabled pending M0.

## Implemented scope

- Immutable candidate Mission IR, bounded typed effects, revision/digest checks
  and an actual Command Center filesystem-verifier bridge test.
- Strict fresh visual identity/semantic binding, immutable observations and
  mutation checks. A disabled synchronous reaction coordinator adds capture
  ordering/coalescing, deadlines, owner interrupt/resume and one immutable
  pending action. Its output is advice to the canonical gate, not authorization.
- Read-only Fleet preflight with existing scheduler filters and conservative
  UNKNOWN for admission requiring live atomic reservations.
- Trusted trace-to-experimental-skill adapter with pinned contract/attempts,
  signed journal checks and actual bounded filesystem readback/revalidation.
- Real local effect/golden fixtures, child-process crash/resume checks and
  a passing replay-denial regression after the authenticated journal repair.
- Offline paired performance arithmetic with input/sample/configuration gates;
  synthetic tests cannot certify real system performance.

These are integration foundations, not a completed owner mission loop. No
new executor, policy store, paid API, production OS input hook or automatic
skill promotion was added. Separate creative-app branches are not included.

## Reproducible validation

Host: local Linux, Python 3.12.13. Command interpreter:
`/tmp/bossman-epoch4-venv/bin/python`; editable Core/Command Center/root packages.
Use `env -u PYTHONPATH` to avoid polluting subprocess installation checks.
Existing Solana package requirements were installed for local import coverage;
no chain operation or paid model call was performed.

| Scope | Command / selection | Result |
|---|---|---|
| Root suite including IR, packaging, metrics and safety | `python -m pytest tests -q --timeout=120` | 247 passed; 3 warnings; 80.06 s at local 618d8a2 code state (later changes documentation/reaction only) |
| Integrated Core selection | `test_v4_visual_state_guard`, `test_epoch4_preflight`, `test_epoch4_verified_skills`, `test_epoch4_golden_foundation`, `test_v3_compound_resume`, `test_v3_evidence_signing`, `test_v3_invariants`, `test_v3_astra_p1`, `test_v3_command_center_adapters`, `test_v3_cross_layer_e2e`, `test_v3_fleet_core`, `test_v3_fleet_safety_proofs`, `test_v3_fleet_e2e` | 272 passed, 1 strict xfailed; 35.66 s at local 13dd176 |
| Command Center targeted truth bridge | `test_epoch4_mission_ir_bridge`, `test_mission010_execution_truth`, `test_no_direct_completed_writes` | 17 passed; 9.21 s at local 13dd176 |
| Offline metrics arithmetic | `tests/test_epoch4_metrics.py` | 17 passed; 0.31 s; synthetic only |
| Reaction + visual | `test_v4_reaction_coordinator`, `test_v4_visual_state_guard` | 71 passed in 0.51 s on integrated local 271c898; independent review also passed |
| Skip registry | `python tools/skips_registry.py --check` | 93 entries, no missing reason at foundation integration; skips do not qualify capabilities |

Selections overlap; counts must not be summed as unique test coverage. Full
Core/Command Center release suites, real Windows desktop, hardware residency,
live owner UX and threefold workload qualification have not passed here.
Warnings are existing Python escape/deprecation notices, not suppressed tests.

## Remaining blocking evidence

Primary fetched again at `d6b43cea0a1127bba7fa2cdabbd80dfa6da681bc`.
Exact-SHA workflow results: root-ci PASS (33991193100), Solana safety PASS
(33991192844), V2 Auto-Repair PASS (33991192881), ASTRA acceptance FAIL
(33991193093), Core CI FAIL (33991192864), Command Center CI FAIL (33991192813).
No new Fable primary commit was observed at this check.

**E4-RT-001 local regression VERIFIED on authenticated journal fix
`7347584b49d25ad366403a6e71e7c132641e6ab1`:** the original raw regression
passes, its xfail is removed, and the stronger test requires load denial on two
fresh-process resumes, unchanged journal bytes and zero duplicate effects.
See `GOLDEN_FOUNDATION.md` for current evidence. Historical suite/workflow
results above are retained as historical facts; this bounded fix does not
close M0 or whole-epoch release gates. Whole signed-snapshot rollback remains
outside this repair and requires reconciliation/independent monotonic state.

Independent peer reviews found and closed visual argument mutation during
observation, Unicode IR handling, and skill readback/state-mutation gaps.
Reaction review covers only its documented single serialized trusted caller;
production input ownership, effect-boundary recheck and measured interrupt
latency require a separate accepted adapter.

## Rollout and rollback

Publish as a draft foundation branch for review. Do not merge/activate on the
failing V3 base. After Fable closes M0, ingest the exact repaired SHA, resolve
semantically and run affected tests plus the required release gates. Rollback
removes the isolated adapters/tests; there is no data migration or new running
observer in this slice. Preserve existing journals, approvals and effect state.
