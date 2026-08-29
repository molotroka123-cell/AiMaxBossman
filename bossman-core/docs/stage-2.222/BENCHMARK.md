# Context Quality Benchmark

Create `tests/fixtures/context_golden/` in the main repo with at least 25 representative tasks: code bug after prior failed fix, latest-vs-old spec, exact numeric constraint, bilingual RU/EN query, decision supersession, browser checkpoint recovery, conflicting memories, long conversation compact, multi-document research, sensitive-source permission filtering.

For every task record expected anchors and expected source IDs. Compare baseline full-context answer against optimized context. Acceptance requires no missing mandatory anchors and no regression in deterministic assertions. Token saving is secondary to correctness.
