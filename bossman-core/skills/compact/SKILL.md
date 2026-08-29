---
name: compact
stage: 2.222
purpose: Compress a long BOSSMAN conversation into a provenance-preserving handoff enriched by memory plugins.
---

# COMPACT SKILL

Use when conversation/context approaches the configured budget or before handing a task to another agent/session.

1. Load recent transcript verbatim.
2. Query configured memory plugins using current objective/project.
3. Extract high-signal older statements; prioritize decisions, constraints, bugs/fixes, paths, versions, numeric parameters, TODOs and explicit user corrections.
4. Build `# COMPACT HANDOFF` using `bossman.context_engine.compact.CompactSkill`.
5. Verify all quality checks.
6. Never claim lossless compression. Raw transcript remains the source of truth.
7. Never write secrets into durable memory.
8. If quality checks fail, increase budget or preserve more verbatim content instead of forcing a smaller summary.
9. Pass memory IDs/source refs forward so the next session can expand any item on demand.
