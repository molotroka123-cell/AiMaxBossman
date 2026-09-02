# Intelligence implementation plan (evidence-ordered)

Status of the substrate after this session: P1 (verification, Deep Fix gates), P2 (learning
corpus), P5 (learning guard) exist; P3/P4/P6/P7 partial. Counts from
`docs/learning/intelligence_ideas/INDEX.md`: IMPLEMENT_NOW 128, PROTOTYPE 44, DEFER 27, REJECT 1.
"IMPLEMENT_NOW" means *small and evidence-backed*, not "this session".

## Done this session

- P1: fresh-evidence verification (F-012), approval identity digest (F-013), Deep Fix Mode gates.
- P2: `learning/` package, schema, split corpora, retrieval, 16 VERIFIED cases, idea catalogue.
- P5 hooks: Deep Fix `verified()` requires an independent verifier; records route by status.
- P6 seeds: SECREM variant tests for paths, URLs, approvals, MCP metadata, sessions.

## Next (ordered by PriorityAid and dependency)

1. **P6 mutator library** (F8.1/F8.2/F8.6/F8.8): extract the boundary mutators from the SECREM
   tests into `tests/_secrem/mutators.py` (both apps) + pinned must-deny ratchet. Enables sibling
   sweep (F8.4).
2. **P7 failing-test-first slices + repo-map cache per HEAD** (F5.1/F5.10/F2.8): tool-side, no
   model; measure tokens per verified task before/after.
3. **P4 verification reserve + evidence-gap escalation** (F7.1/F7.2/F7.3) in the agent task
   templates and Deep Fix runner; checkpoint-or-stop rule.
4. **P3 outcome table, collected for free** (F1.6/F2.9/F6.3/F6.8): log (model, class, failure
   pattern, help level, verified?) from existing events; no behaviour change until n ≥ 20.
5. **Retrieval upgrades that need no data** (F1.1/F1.2/F1.5/F1.7/1.3/1.7/1.9): predicate index,
   counterfactual pairs, supersession, confidence gate.
6. **Verifier isolation + plan hash before patch** (F4.1/F4.2/F4.7): Deep Fix runner wiring in the
   command-center engine (behind the flag).
7. **Lab**: holdout set with hashes (F10.2), experiments.jsonl, honesty rule (F10.3), gate
   ablation (F10.6).
8. PROTOTYPEs only after 4 has data: failure-pattern routing (F6.1), SPRT stopping (7.1), calibration
   (3.10/F3.9).

## Explicit non-goals until measured

Per-model profiles/skills, bandits, Pareto archives, LLM hypothesis merging, protocol mutation,
concurrency perturbation. See `FABLE_ADDITIONAL_INTELLIGENCE_RECOMMENDATIONS.md` for reasons.
