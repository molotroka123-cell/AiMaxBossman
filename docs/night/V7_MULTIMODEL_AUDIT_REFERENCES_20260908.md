# V7 Multi-Model Audit References — mandatory input for night convergence

This file is a correction/addendum to `NIGHT_V7_CONVERGENCE_MASTER_20260908.md`.

The night integrator MUST NOT design or implement V7 from the Phase-1 branch alone. A substantial V7 architecture/audit effort was already performed by multiple frontier-model passes on separate branches and then converged. Those branches are evidence/reference inputs, not branches to merge blindly.

## Mandatory branches / pinned tips

Read these before making further V7 architectural decisions:

1. `v7/adaptive-reality-os-20260907` @ `314feb6dfc038169a8e08c3854a087ba9cb65112`
   - canonical V7 Adaptive Reality OS charter/architecture/workspace and transition material.

2. `v7/adaptive-reality-os-audit-20260907` @ `c61151ee4c1fb1d875c517728be58ad4f0de5f7e`
   - independent V7 audit/correction line; includes explicit V6 freeze-barrier reasoning.

3. `v7/audit-convergence-20260907` @ `a548050f382887cc937da4ce02738d4217ff39a1`
   - V7 audit convergence / prompt pack / launch ordering. Treat this as an important synthesis input rather than another isolated opinion.

4. `v7/frontier-multimodel-audit-20260907` @ `94594b6c888d9c8ef356f1a35f5d8fbfb13ba7c9`
   - frontier-model audit intake/reference line.

5. `v7/multi-model-architecture-audit-20260907` @ `f33618359d6127ba1b009a29288278a61437db28`
   - adversarial multi-model architecture synthesis protocol.

6. `v7/multi-model-audit-20260907` @ `2b7080b6ddf3568bde91c7066bce9721f71e65b8`
   - GPT-5.6 Sol independent V7 audit.

7. `audit/perplexity-v7-independent-20260907` @ `d08814eeff6d049343c6ffcd88ff3acc91491c19`
   - Perplexity independent V7 architecture audit.

8. `v7/phase1-reality-core-20260908` @ `17f1131287774a969e1c68ab2606c51bb9055113`
   - first actual V7 runtime Phase-1 implementation: typed Mission IR, World State Graph, deterministic strategy candidates and shadow utility routing.

## Integration rule

Before extending V7:

1. inspect the files/commits unique to every branch above;
2. build a compact requirement/finding matrix;
3. identify consensus, disagreements, stale assumptions and already-implemented recommendations;
4. map every still-valid recommendation to the CURRENT night-branch code, not to the old audited SHA;
5. implement the highest-value repository-fixable items that fit the convergence mission;
6. preserve useful ideas even when the original branch is stale;
7. do NOT merge old branches wholesale if that would reintroduce old V6 code or regress current OpenHands/runtime fixes.

Use `git show`, `git diff`, `git log`, `git merge-base`, selective cherry-pick/manual porting as appropriate.

## Important freshness rule

Most of these V7 audits were created against older V6 snapshots. Their architecture findings are reference material, NOT current runtime truth.

Current runtime/fix truth comes from the night branch and its latest integrated ancestors. Revalidate every finding against current code before changing anything.

Do not resurrect a finding that current code already closes.

## Expected output from this synthesis

Do not spend the night merely producing another audit document. The synthesis should directly improve implementation.

At minimum, use the multi-model corpus to challenge and improve:

- Reality Compiler / Mission IR execution-contract boundaries;
- World State Graph provenance, freshness, conflict and uncertainty semantics;
- deterministic + model-assisted strategy generation;
- expected-utility / risk / cost / latency / resource-aware routing;
- strategy-changing recovery instead of blind retries;
- local/cloud Cognitive Fabric and model capability routing;
- resource-memory/model-residency awareness;
- attention/QoS and mission prioritization;
- skill learning/compiler/promotion boundaries;
- dynamic mission-team organization;
- owner-facing Reality/Mission UX;
- proof/effect/post-state integration with existing V4-V6 authority gates.

## Non-negotiable authority rule

V7 intelligence must sit ABOVE and USE the existing V4-V6 safety substrate, not bypass it.

`strategy score != permission`
`model confidence != evidence`
`MissionIR != authorization`
`WorldState belief != verified external truth`
`skill promotion != self-granted capability`

If an audit recommendation conflicts with a newer proven safety invariant, preserve the invariant and adapt the recommendation.

## Night-run completion expectation

The integrator may improve beyond these audits. They are a floor/reference corpus, not a ceiling.

After mandatory P0/B2-B5/OpenHands work is stable, use remaining capacity to implement the strongest still-valid V7 recommendations from this multi-model corpus, with tests and measurable acceptance evidence.
