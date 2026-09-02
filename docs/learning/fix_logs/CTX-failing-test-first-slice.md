# Learning Case: CTX-failing-test-first-slice

## Metadata
MODEL: claude-fable-5-1
AGENT: fable-lead
START_SHA: 6668ea6
END_SHA: HEAD+1
LEARNING_STATUS: VERIFIED
OUTCOME: FIXED
VERIFIED_BY: pytest:tests/test_context_slice.py
CONFIDENCE: 0.8
TAGS: {"bug_class": "discovery_cost", "component": "tools.context_slice", "domain": "efficiency", "severity": "INFO"}
FINDINGS: 

## Task
Tool-side context compiler: repo map cached per HEAD sha + failing-test-first slice (ideas F5.1, F5.10, F2.8)

## Symptom
Every session/agent re-read large parts of the repository to find the code relevant to a failing test; discovery, not reasoning, exhausted the usage budget (see OPS-parallel-agents-usage-limit).

## Reproduction
- tests/test_context_slice.py::test_real_secrem_test_slice_is_a_small_fraction_of_the_app

## Evidence
- measured: slice for command-center/tests/test_secrem_f015_self_assert.py = 13,251 tokens vs whole bcc app 445,209 tokens (ratio 0.03)
- repo map cached by sha: second call on the same sha is a cache hit even after file edits; a new sha rebuilds

## Hypotheses considered
- no deterministic discovery artefact existed; only model-driven reads

## Rejected hypotheses + why
- embedding-based retrieval first (needs an index build and a model; the ast/import graph is exact and free)

## Root cause
Discovery was performed by the model on every task instead of by a tool once per commit.

## Relevant code paths
- tools/context_slice.py:repo_map
- tools/context_slice.py:failing_test_slice

## Fix strategy
ast-based repo map (files, top-level symbols, sha256, token estimate) cached under .bossman-cache/repo_map-<sha>.json; slice = test file + transitively imported repo modules to a depth bound, emitted as a hashed manifest with reason codes (F5.5) in a deterministic order.

## Alternatives considered
- file-level grep heuristics (non-deterministic across queries)
- full call-graph slicing (F5.3, deferred: needs per-language parsing)

## Why this fix was chosen
Exact, deterministic, model-free; a 30× reduction for the common fix task shape, measured.

## Files changed
- tools/context_slice.py
- .gitignore

## Tests added
- tests/test_context_slice.py

## Original reproduction after fix
ratio 0.03 measured on a real SECREM test

## Adversarial variants
- depth bound respected (depth-3 module excluded at depth 2)
- unrelated module excluded
- cache hit despite file edit on same sha (documents the invalidation contract: sha, not mtime)

## Regression
tests/test_context_slice.py 3 passed

## Fresh external verification
pytest with fresh temporary repos; token counts computed from file bytes

## Generalizable lessons
- Discovery artefacts should be keyed by commit, produced by tools, and handed to models as hashed manifests

## Teach local model
- Recognize: an agent reading whole packages to find one function
- Prefer: python tools/context_slice.py slice <app> <failing test>
- Verify using: manifest hashes match disk before editing

## Limitations / follow-up
- token estimate is len/4, not a tokenizer
- imports only (no dynamic/feature auto-loading resolution)
- not yet wired into agent templates or the engine
