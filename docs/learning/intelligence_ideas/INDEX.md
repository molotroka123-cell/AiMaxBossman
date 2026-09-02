# Intelligence ideas — catalogue and decisions

Total 200 (owner 100, Fable 100). Decisions: DEFER=27, IMPLEMENT_NOW=128, PROTOTYPE=44, REJECT=1

PriorityAid = EVI/(cost+0.5) − risk penalty, EVI = benefit×confidence×reuse×coverage (ordinal aid, not truth).

## Shared primitives

- **P1** — Evidence ledger: типизированные наблюдения с provenance/TTL/hash (есть: bcc/v2/verification, flight_recorder)
- **P2** — Learning corpus + retrieval с фильтрами и статусами (есть: learning/trace.py)
- **P3** — Per-(model,task_class,failure_pattern) outcome table (частично: router class stats, failure_patterns)
- **P4** — Budgeted decision loop: expected-gain vs cost с ранней остановкой (частично: uncertainty, adaptive compute)
- **P5** — Hidden holdout + promotion pipeline с независимой верификацией (есть: learning_guard)
- **P6** — Deterministic mutators/counterexample generators над типизированными инвариантами (частично: redteam PoCs, variants)
- **P7** — Context slots с бюджетами и attribution (частично: bossman/context.py budget shares)

## M1: Experience Retrieval Engine

| id | src | decision | aid | summary | evidence |
|---|---|---|---|---|---|
| F1.1 | FABLE | IMPLEMENT_NOW | 8.4 | Retrieval by INVARIANT VIOLATION signature, not by text: index cases by (boundary, violated predicate) e.g. 'resolved-path ∉ roots' | Security bugs recur as the same predicate violation across components; symptom text differs. Cheap: predicates are already in generalizable_lessons. |
| F1.2 | FABLE | IMPLEMENT_NOW | 8.4 | Counterfactual-pair retrieval: return the case AND the tempting wrong fix that was rejected, as a pair | Local models copy the first plausible fix; showing the rejected alternative with its evidence prevents the common failure mode. Fields exist. |
| 1.3 | OWNER | IMPLEMENT_NOW | 5.6 | Temporal decay with timeless-invariant exception | Cheap: tag lessons as invariant vs implementation-detail; decay only the latter. Prevents stale code paths from being retrieved as truth. |
| 1.6 | OWNER | IMPLEMENT_NOW | 5.6 | Token-aware compression with progressive disclosure | compact() exists; add depth-2 (evidence) on demand. Directly serves token economy. |
| 1.7 | OWNER | IMPLEMENT_NOW | 5.6 | Retrieval confidence gate (inject nothing when weak) | Misleading context is worse than none; threshold on tag-match count is trivial. |
| 1.9 | OWNER | IMPLEMENT_NOW | 5.6 | Case lineage/versioning tied to code architecture | Store end_sha + code paths (done); invalidate when paths vanish — a cheap staleness check. |
| F1.3 | FABLE | IMPLEMENT_NOW | 5.6 | Retrieval budget as function of uncertainty: 0 cases at L0, up to 8 at L4 | Ties retrieval cost to classify_reasoning level; avoids paying context for trivial tasks. |
| F1.4 | FABLE | IMPLEMENT_NOW | 5.6 | Evidence-first ordering: put the decisive observation before the narrative in the injected snippet | Attention is front-loaded; the decisive test is what the model must reproduce. Zero cost (render order). |
| F1.5 | FABLE | IMPLEMENT_NOW | 5.6 | Provenance-signed cases: case_id + end_sha + verifier in every injected snippet so the model can cite, not paraphrase | Prevents 'memory' being upgraded to authority; matches F-006 marker policy. |
| F1.7 | FABLE | IMPLEMENT_NOW | 5.6 | Anti-retrieval list: cases whose lessons were later invalidated by a VERIFIED case get a 'superseded_by' link and are never injected | Lineage (1.9) done right: supersession is explicit, not decay-by-time. |
| F1.9 | FABLE | IMPLEMENT_NOW | 5.6 | Cross-app case bridging: tag whether a lesson transfers bossman-core↔command-center (two apps, no shared code) | This repo's specific structure; the same SSRF fix was needed three times (net.py, browser, discovery). |
| F1.10 | FABLE | IMPLEMENT_NOW | 5.6 | Retrieval regression test: fixed queries must return fixed case ids (golden set) so store changes can't silently degrade retrieval | Retrieval is code; test it like code. |
| 1.4 | OWNER | IMPLEMENT_NOW | 4.6 | Negative retrieval of counterexamples/FAILED_EXPERIMENT | Already supported (include_failed + retrieval_warning). Make it default for security bug classes. |
| F1.8 | FABLE | IMPLEMENT_NOW | 1.4 | Tool-output-keyed retrieval: trigger on error signatures from fresh tool output (classify_error) rather than on the task prompt | The failure pattern is known only after the first test run; retrieving at that moment is where cases help. |
| 1.1 | OWNER | PROTOTYPE | 1.33 | Hybrid retrieval: embeddings + BM25 + code-path/symbol similarity | BM25+symbol match cheap and offline; embeddings already in context_engine. Value only measurable once corpus > ~50 cases. |
| 1.5 | OWNER | PROTOTYPE | 0.67 | Query decomposition by symptom/component/invariant then fuse | Filters exist (tags); fusion needs scoring. Prototype as weighted union. |
| F1.6 | FABLE | PROTOTYPE | 0.67 | Retrieval dry-run audit: log which cases were injected per run without acting on them, to build the P3 usefulness table for free | Turns 1.8/1.10 from 'needs data' into 'collecting data' at zero behaviour risk. |
| 1.2 | OWNER | DEFER | 0.53 | MMR/diversity selection across failure modes | Corpus is 16 cases; diversity is moot. Reconsider at >100. |
| 1.8 | OWNER | DEFER | -0.68 | Cross-model usefulness scoring per case | Needs P3 outcome table populated by real runs; no data yet. |
| 1.10 | OWNER | DEFER | -1.68 | Retrieval outcome feedback updating ranking | Requires attribution data (P3); Goodhart risk if cases 'win' by being injected often. |

## M2: Reasoning Compiler

| id | src | decision | aid | summary | evidence |
|---|---|---|---|---|---|
| F2.3 | FABLE | IMPLEMENT_NOW | 8.4 | Refusal-as-first-class output: protocol may end in NOT_REPRODUCIBLE/BLOCKED_ENV with a machine-readable next action instead of a fabricated fix | Prevents false-success; Deep Fix already has failure states — make every protocol have them. |
| 2.3 | OWNER | IMPLEMENT_NOW | 5.6 | Early-exit on decisive evidence | Evidence ledger + stop rule; falls out of P4. |
| 2.6 | OWNER | IMPLEMENT_NOW | 5.6 | Plan linting (missing verification, cycles, unauthorized effects) | CompiledTask.ordered() detects cycles; add 'every mutating step has verification' lint. |
| F2.7 | FABLE | IMPLEMENT_NOW | 5.6 | Protocol cost preview: before starting, print (states, expected tokens, expected tool calls) and require it to fit the reservation | Budget awareness at plan time; pairs with 7.4. |
| 2.2 | OWNER | IMPLEMENT_NOW | 4.6 | Difficulty estimator selects protocol depth | classify_reasoning L0–L4 exists (V2.6 B); wire it to choose Deep Fix vs direct. |
| 2.5 | OWNER | IMPLEMENT_NOW | 2.8 | Constraint compiler → machine-checkable gates | This is exactly what Deep Fix gates and verification ExpectedState are. Extend with owner invariants list. |
| F2.1 | FABLE | IMPLEMENT_NOW | 2.8 | Gate-as-code, not gate-as-prompt: every protocol step is a Python predicate over the evidence ledger (Deep Fix pattern) — prompts only explain | A model can skip a prompted step; it cannot skip a state machine. Already the Deep Fix design; generalize. |
| F2.5 | FABLE | IMPLEMENT_NOW | 2.8 | Two-phase commit for plans: 'declare expected effects' → 'execute' → 'diff observed vs declared' (unexpected effects = failure even if tests pass) | Catches broad diffs and side effects; observed/declared diff is the strongest anti-drift check. |
| F2.8 | FABLE | IMPLEMENT_NOW | 2.8 | Deterministic scaffolding steps run without a model (repo map, test discovery, diff stat) — the model sees results only | Largest token saver: discovery is mechanical. This session's cost was discovery. |
| F2.9 | FABLE | IMPLEMENT_NOW | 2.8 | Protocol signatures in learning records (which states ran, in what order) for later ablation | Free telemetry (Deep Fix history) — enables 10.9. |
| F2.10 | FABLE | IMPLEMENT_NOW | 2.8 | Human-checkpoint scheduling as protocol step: OWNER_DECISION_REQUIRED emitted with a bounded question set | Avoids blocking loops on unanswerable questions; state exists in Deep Fix. |
| F2.6 | FABLE | IMPLEMENT_NOW | 1.87 | Compile owner invariants into ExpectedState templates once; protocols reference them by id | Owner constraints become data (verification.py ExpectedState), reused across tasks. |
| 2.1 | OWNER | IMPLEMENT_NOW | 0.93 | Composable micro-protocols instead of monolithic workflows | Deep Fix gates are already modular states; expose them as protocol fragments. |
| F2.4 | FABLE | PROTOTYPE | 0.8 | Protocol replay from the flight recorder: re-run a past protocol against current HEAD to detect regressions of the fix itself | Turns recorded protocols into regression tests. |
| 2.10 | OWNER | PROTOTYPE | 0.67 | Failure-specific recovery protocols from verified signatures | failure_patterns.recommended_recovery exists; link to Deep Fix hypotheses seed. |
| F2.2 | FABLE | PROTOTYPE | 0.33 | Minimal-sufficient-protocol search: start from the 3-step protocol and add a step only when a past FAILED_EXPERIMENT of this class shows it was needed | Directly attacks ceremonial token waste with negative knowledge. |
| 2.7 | OWNER | DEFER | 0.16 | Domain adapters (coding/security/research/ops/data/business) | Premature; one adapter (coding/security) is enough until measured. |
| 2.4 | OWNER | PROTOTYPE | -0.33 | Protocol switching on contradiction | Needs contradiction detector (uncertainty.contradiction exists as scalar). |
| 2.8 | OWNER | PROTOTYPE | -0.33 | Protocol outcome telemetry (ceremonial-step detection) | Record per-state duration/tokens in Deep Fix history; analyze later. |
| 2.9 | OWNER | DEFER | -3.54 | Protocol mutation sandbox on hidden evals | Self-improvement lab prerequisite (P5) and evals corpus needed. |

## M3: Hypothesis Tournament

| id | src | decision | aid | summary | evidence |
|---|---|---|---|---|---|
| F3.1 | FABLE | IMPLEMENT_NOW | 8.4 | Hypothesis must name its DISCRIMINATING OBSERVATION up front; hypotheses without one are not admitted | Forces falsifiability; cheapest anti-anchoring device. |
| 3.3 | OWNER | IMPLEMENT_NOW | 5.6 | Cheap-test-first scheduling by info gain per token | Order discriminating tests by (cost, expected split) — heuristic is enough. |
| 3.7 | OWNER | IMPLEMENT_NOW | 5.6 | Stop rule on posterior margin + verification sufficiency | Stop when one hypothesis survives all discriminating tests; no fixed K. |
| F3.2 | FABLE | IMPLEMENT_NOW | 5.6 | Tournament by elimination with cached observations: each test result is a fact reused across all hypotheses (no re-running) | Evidence ledger dedups tests; cuts tool calls. |
| F3.4 | FABLE | IMPLEMENT_NOW | 5.6 | Hypothesis debt: unresolved hypotheses are stored as open questions in the learning record instead of being dropped | Negative knowledge and follow-ups become retrievable. |
| F3.6 | FABLE | IMPLEMENT_NOW | 5.6 | Symmetric-evidence rule: an observation that is consistent with all hypotheses has zero weight and is not recorded as support | Prevents confirmation-by-volume in prose. |
| F3.8 | FABLE | IMPLEMENT_NOW | 5.6 | Bounded tournament: max 5 hypotheses, max 3 rounds; overflow → OWNER_DECISION_REQUIRED with the ledger | Bounds tokens and time deterministically. |
| F3.10 | FABLE | IMPLEMENT_NOW | 5.6 | Tournament transcripts are learning material only after verification (else FAILED_EXPERIMENT) | Consistent with corpus policy. |
| 3.10 | OWNER | PROTOTYPE | 4.0 | Postmortem calibration of predicted probabilities | Log predicted top hypothesis vs verified root cause in learning record (field exists) — analysis later. |
| 3.1 | OWNER | IMPLEMENT_NOW | 2.8 | Evidence-disagreement generator (discriminating tests) | Core of hypothesis tournament: ask 'which observation splits H1/H2' — make it a required field before patch. |
| F3.7 | FABLE | IMPLEMENT_NOW | 2.8 | Root-cause acceptance requires reproducing the bug FROM the cause (forward reproduction), not just explaining it | Strongest test of a causal claim; Deep Fix gate extension. |
| F3.3 | FABLE | PROTOTYPE | 2.0 | 'Wrong-layer' detector: if all hypotheses live in the same file/layer, force one hypothesis one layer up/down | Empirically root causes cross layers (F-005 was 'a second executor path', not the quoting). |
| 3.4 | OWNER | IMPLEMENT_NOW | 1.4 | Contrarian hypothesis slot | Zero cost: require one structurally different hypothesis in root_cause_hypotheses. |
| F3.5 | FABLE | PROTOTYPE | 1.33 | Cheap-probe library per failure class (e.g., loop-affinity → 'run twice under asyncio.run') | Turns failure_patterns into executable probes. |
| F3.9 | FABLE | PROTOTYPE | 1.0 | Calibration by construction: record P(top hypothesis) as ordinal (likely/possible/unlikely) and score Brier over time | Ordinal avoids fake precision; enables 3.10. |
| 3.5 | OWNER | PROTOTYPE | 0.33 | Dependency-aware hypotheses (root vs symptom) | Model as parent links between hypotheses; benefit unmeasured. |
| 3.6 | OWNER | REJECT | 0.0 | Automatic hypothesis merging | Semantic-equivalence detection by LLM adds tokens for little gain at ≤5 hypotheses. |
| 3.2 | OWNER | DEFER | -0.68 | Bayesian posterior calibrated from historical root-cause frequencies | No frequency table yet; prose priors would be fake precision. |
| 3.8 | OWNER | DEFER | -0.68 | Historical prior conditioning by component/error class | Needs P3. |
| 3.9 | OWNER | DEFER | -0.68 | Multi-agent hypothesis generation gated by expected diversity | Cost high; evidence of diversity benefit absent. Try after single-agent baseline. |

## M4: Verifier-First Development

| id | src | decision | aid | summary | evidence |
|---|---|---|---|---|---|
| 4.2 | OWNER | IMPLEMENT_NOW | 16.8 | Evidence hierarchy (observation > independent impl > model judgment) | Already encoded in F-012 fix (text can veto, never approve); document as policy levels. |
| 4.5 | OWNER | IMPLEMENT_NOW | 16.8 | Anti-echo verifier | Done for review gate (F-012). Extend to Deep Fix: evidence derived from coder text is not evidence. |
| 4.4 | OWNER | IMPLEMENT_NOW | 11.2 | Freshness TTL on observations | observed_at exists; add max-age check in verify_all. |
| 4.6 | OWNER | IMPLEMENT_NOW | 11.2 | Negative-control tests that must stay failing/denied | Security fixes ship with must-deny tests already; make it a Deep Fix gate for security bug classes. |
| 4.9 | OWNER | IMPLEMENT_NOW | 11.2 | Risk-adaptive verification budget | risk_class already on CompiledTask; map to required evidence count. |
| F4.5 | FABLE | IMPLEMENT_NOW | 11.2 | Evidence sufficiency by risk class: irreversible → 2 independent observation kinds; sensitive → 1 observation + negative control; normal → 1 observation | Deterministic rule from CompiledTask.risk_class. |
| F4.6 | FABLE | IMPLEMENT_NOW | 11.2 | Verification results are events on the bus (evaluation.completed exists) with the evidence hash so dashboards and Learning Guard consume the same fact | One source of truth for 'verified'. |
| F4.8 | FABLE | IMPLEMENT_NOW | 11.2 | Verifier cannot be the same model FAMILY as the coder when the effect is not observable (judgment-only) — else result is UNVERIFIED | Correlated errors; encode as policy in Deep Fix verifier check. |
| F4.9 | FABLE | IMPLEMENT_NOW | 11.2 | Time-boxed verification with explicit UNVERIFIED on timeout (never PASS by timeout) | Fail-closed default for verification. |
| F4.10 | FABLE | IMPLEMENT_NOW | 11.2 | Verification recipes stored per bug class in the corpus ('Verify using:') and retrieved before patching | Field already captured; retrieval hook is trivial. |
| 4.1 | OWNER | IMPLEMENT_NOW | 5.6 | Verification plan hashed/bound to the task before patch | Binds expected state before coding (anti goalpost); reuse approval_digest style hash. |
| F4.1 | FABLE | IMPLEMENT_NOW | 5.6 | Verification plan is written as ExpectedState JSON and stored BEFORE the patch (hash in the run); patch cannot change it | Concrete form of 4.1 using existing verification.py types. |
| F4.2 | FABLE | IMPLEMENT_NOW | 5.6 | Verifier isolation: the verifier process gets only the plan + read tools, never the coder's transcript | Removes echo channel entirely (stronger than anti-echo filtering). |
| F4.7 | FABLE | IMPLEMENT_NOW | 5.6 | 'Effect diff' verification: snapshot of declared-writable paths before/after; any change outside the declared set fails verification | Catches collateral writes; pairs with F2.5. |
| 4.3 | OWNER | PROTOTYPE | 2.67 | Two-channel verification for high-risk actions | For irreversible effects: require two independent observers (e.g., file hash + DB row). |
| F4.3 | FABLE | PROTOTYPE | 2.67 | Negative controls auto-derived: for every 'must allow' expectation generate the mirrored 'must deny' case from the same policy | Halves the cost of writing security tests; mutator over ExpectedState. |
| 4.10 | OWNER | PROTOTYPE | 1.33 | Verification provenance graph | Evidence ledger links expected→action→observed; graph view is a rendering of P1. |
| F4.4 | FABLE | PROTOTYPE | 1.33 | Observation freshness proven by nonce: verifier writes a nonce into the environment and expects it in the observation path (e.g., temp file) to detect cached observers | Detects stale/cached observation sources. |
| 4.7 | OWNER | PROTOTYPE | 0.8 | Metamorphic verification | Useful for non-exact outputs (research/media); needs per-domain relations. |
| 4.8 | OWNER | DEFER | -0.36 | Verifier calibration dataset (FP/FN by verifier type) | Needs volume; log verdict+later outcome first. |

## M5: Automatic Context Compiler

| id | src | decision | aid | summary | evidence |
|---|---|---|---|---|---|
| 5.2 | OWNER | IMPLEMENT_NOW | 8.4 | Change-impact context from recent commits/diff | git diff/log is free; prioritize touched symbols. Big win for fix tasks. |
| F5.7 | FABLE | IMPLEMENT_NOW | 8.4 | Untrusted-data slot is separate from code/evidence slots and always framed (F-006 marker) — compiler enforces, not the caller | Security property of the context compiler itself. |
| F5.9 | FABLE | IMPLEMENT_NOW | 8.4 | Prompt-cache-aware layout: stable prefix (policies, invariants, tool schemas) first, volatile slices last, so cached-prefix hit rate is maximal | Direct token/latency saver; core context.py already orders blocks for KV cache — extend to slices. |
| 5.3 | OWNER | IMPLEMENT_NOW | 5.6 | Staleness invalidation on HEAD/tool observation change | Tie context cache key to HEAD sha + observation ids. |
| 5.6 | OWNER | IMPLEMENT_NOW | 5.6 | Context slots with explicit budgets | BLOCK_SHARES already exist in core context.py; add 'evidence' and 'experience' slots. |
| F5.5 | FABLE | IMPLEMENT_NOW | 5.6 | Context receipts: every injected block carries a reason code (test-import, diff-touched, retrieved-case) for later attribution without instrumentation | Makes 5.10 possible cheaply. |
| F5.8 | FABLE | IMPLEMENT_NOW | 5.6 | Context replay for verification: verifier gets the same manifest hashes to prove it looked at the same code | Ties context to evidence. |
| 5.5 | OWNER | IMPLEMENT_NOW | 2.8 | Progressive context expansion | Start with targeted slice; expand only when verifier/uncertainty demands (P4). |
| F5.1 | FABLE | IMPLEMENT_NOW | 2.8 | Failing-test-first context: the failing test + the code it imports (transitively, depth 2) is the default context; nothing else until asked | Most fix tasks resolve within this slice; measured in this session by how little of the repo mattered per finding. |
| F5.10 | FABLE | IMPLEMENT_NOW | 2.8 | Repo map cached per HEAD and shared across agents/sessions (file, not prompt) | Discovery cost paid once per commit, not per session. |
| F5.2 | FABLE | IMPLEMENT_NOW | 1.87 | Context as a manifest with hashes: the model receives file@sha slices; stale slices are refused at tool time | Prevents editing from stale reads (a classic local-model failure). |
| F5.6 | FABLE | IMPLEMENT_NOW | 1.87 | Hard cap per slot with overflow → summary-by-tool (deterministic: signatures + docstrings), never summary-by-model | Deterministic compression cannot hallucinate. |
| 5.8 | OWNER | PROTOTYPE | 1.33 | Context contamination detector | Flag docs older than code paths they describe; unverified memories already tagged (F-006). |
| F5.4 | FABLE | PROTOTYPE | 1.33 | 'What changed since the last VERIFIED case' diff as a context block for recurring components | Links corpus lineage to context. |
| F5.3 | FABLE | PROTOTYPE | 1.2 | Symbol-level rather than file-level slices via existing code_index; include only referenced symbols | Bigger saver than file slices; index already has symbols. |
| 5.1 | OWNER | PROTOTYPE | 0.8 | AST/symbol/call-graph slicing | code_index exists (BM25 over symbols); slicing by call graph is a real token saver but needs tree-sitter or ast per language. |
| 5.4 | OWNER | DEFER | 0.53 | Redundancy clustering | Marginal at current context sizes; measure first. |
| 5.7 | OWNER | PROTOTYPE | -0.2 | Loss-aware compression test | Check that compressed context still answers invariant questions — needs an eval harness. |
| 5.9 | OWNER | DEFER | -0.84 | Per-model context profiles | Needs P3 data. |
| 5.10 | OWNER | DEFER | -1.54 | Context attribution telemetry | Attribution is expensive to estimate honestly; ablation-based only in the lab. |

## M6: Dynamic Model Router

| id | src | decision | aid | summary | evidence |
|---|---|---|---|---|---|
| F6.2 | FABLE | IMPLEMENT_NOW | 8.4 | Local-first escalation ladder with verified checkpoints: local attempt must produce a reproducible failing test before cloud is considered | Cloud pays for uncertainty, not for discovery; also security-aligned (fail-closed cloud). |
| F6.7 | FABLE | IMPLEMENT_NOW | 8.4 | Route audit invariant test: for every task with cloud disabled, model_calls.is_cloud must be false (nightly self-check over the DB) | Continuous verification of F-008/F-016. |
| F6.5 | FABLE | IMPLEMENT_NOW | 5.6 | Capability probes are verification tasks: a model earns a capability tag only by passing a fresh probe (exists: model_capability_checks) — never by self-description | Already partially; make advertised caps non-scoring when a probe exists. |
| F6.10 | FABLE | IMPLEMENT_NOW | 5.6 | Route by context fit: candidate window must fit the compiled manifest with headroom for evidence; else route up or compress deterministically | Prevents silent truncation failures. |
| 6.10 | OWNER | IMPLEMENT_NOW | 2.8 | Provider reliability prior (outages/429/schema compliance) | Circuit breaker + 429 counters exist; fold into candidate score. |
| F6.3 | FABLE | IMPLEMENT_NOW | 2.8 | Router decisions carry an explanation record in the learning corpus when the task ends (chosen/rejected/why/outcome) | Router already stores route explanations; add outcome link. |
| F6.4 | FABLE | IMPLEMENT_NOW | 2.8 | Warm-model bias with explicit cost: switching cost (load time × VRAM) is part of the score, not a tie-breaker | Formalizes 6.9. |
| F6.6 | FABLE | IMPLEMENT_NOW | 2.8 | Refuse-to-route: when no candidate meets evidence-based requirements, return OWNER_DECISION_REQUIRED rather than best-effort | Fail-closed routing for high-risk classes. |
| F6.8 | FABLE | IMPLEMENT_NOW | 2.8 | Per-model output-schema compliance counter (tool-call JSON validity) feeding the reliability prior | Cheap counter; local models fail here most. |
| 6.9 | OWNER | IMPLEMENT_NOW | 1.4 | Resource-aware local scheduling (VRAM residency) | Model swaps dominate local latency; Resource Brain lease exists — prefer resident model when scores tie. |
| 6.7 | OWNER | PROTOTYPE | 1.33 | Model+context-budget joint optimization | Real window (real_window) exists; joint choice is an ordering rule. |
| 6.3 | OWNER | IMPLEMENT_NOW | 0.93 | Drift detector for model/provider changes | Key stats by model version/date; decay old evidence — small change to class stats. |
| 6.6 | OWNER | PROTOTYPE | 0.33 | Model+protocol joint routing | Choose (model, Deep Fix depth) jointly; natural once 2.2 lands. |
| 6.8 | OWNER | DEFER | 0.27 | Deadline-aware routing | No deadline signal in tasks today. |
| 6.1 | OWNER | PROTOTYPE | -0.33 | Bayesian competence posteriors per task class | Beta posterior over per-(model,class) success is cheap; router class stats exist (n>=5 rule). Extend with intervals. |
| 6.4 | OWNER | PROTOTYPE | -0.33 | Failure-mode router (known weaknesses) | Route away from models with high rate of a failure pattern; depends on P3 population. |
| F6.1 | FABLE | PROTOTYPE | -0.33 | Route on FAILURE-PATTERN posterior, not on average success: P(model solves / this error class) | Sharper than class-average; needs P3 rows keyed by classify_error. |
| F6.9 | FABLE | DEFER | -0.36 | Shadow-route: run the cheaper candidate in shadow on a sample of tasks to build competence data without risking outcomes | Needs shadow lane wiring. |
| 6.5 | OWNER | DEFER | -0.68 | Ensemble escalation when disagreement resolution worth cost | Cost model needed first. |
| 6.2 | OWNER | DEFER | -2.36 | Contextual bandit exploration on shadow tasks only | Requires shadow lane + volume. |

## M7: Adaptive Compute / Escalation

| id | src | decision | aid | summary | evidence |
|---|---|---|---|---|---|
| 7.4 | OWNER | IMPLEMENT_NOW | 8.4 | Budget reservation for verification | Reserve a fixed share of task budget for verify/commit; trivial and high value against 'spent it all generating'. |
| 7.8 | OWNER | IMPLEMENT_NOW | 8.4 | Deadline checkpoint policy (stop early to verify/commit/push) | This session's own lesson: reserve time for checkpoint; encode as a gate in Deep Fix/agent templates. |
| F7.1 | FABLE | IMPLEMENT_NOW | 8.4 | Verification-first budgeting: allocate verify budget before generate budget; generation stops when verify reserve would be breached | Inverse of the usual order; protects the only thing that matters (VerifiedSuccess). |
| F7.2 | FABLE | IMPLEMENT_NOW | 8.4 | Escalate on EVIDENCE GAP, not on failure count: retry with the same model only if a new observation was obtained | Failure count is a proxy; evidence gap is the cause. |
| F7.9 | FABLE | IMPLEMENT_NOW | 8.4 | Escalation requires a handoff packet: failing test, hypotheses, ledger — the stronger model never re-discovers | Biggest cloud-cost saver: strong models start at the frontier. |
| 7.2 | OWNER | IMPLEMENT_NOW | 5.6 | Retry diversity requirement | Governor already stops identical-error loops; require changed strategy signature per retry. |
| 7.9 | OWNER | IMPLEMENT_NOW | 5.6 | Cloud escalation requires predicted gain + policy approval | Policy side exists (fail-closed); add gain threshold from uncertainty before requesting approval. |
| F7.4 | FABLE | IMPLEMENT_NOW | 5.6 | Compute ladder with fixed rungs (local-fast → local-strong → cloud) and a hard cap on rungs per task class | Deterministic escalation; cost bounded. |
| F7.5 | FABLE | IMPLEMENT_NOW | 5.6 | Cost of being wrong estimates from risk_class drive the verification rung, not the generation rung | Spend on checking where mistakes are expensive. |
| F7.6 | FABLE | IMPLEMENT_NOW | 5.6 | Early-stop on 'no new hypothesis' — if a retry produces the same hypothesis set, stop and escalate | Concrete diversity check (7.2). |
| F7.8 | FABLE | IMPLEMENT_NOW | 5.6 | Cheap parallel probes are deterministic tools only (never parallel model calls) — parallelism budget spent on tests, not tokens | Clarifies 7.6 to avoid token blowups. |
| F7.3 | FABLE | IMPLEMENT_NOW | 4.2 | Checkpoint-or-stop rule: any unit of work larger than N minutes must produce a committed artifact or a recorded PARTIAL state | This session's operational lesson; encode in agent templates. |
| 7.6 | OWNER | IMPLEMENT_NOW | 1.87 | Parallel cheap probes before expensive escalation | Run discriminating cheap tests concurrently (they are deterministic) before a strong-model call. |
| F7.10 | FABLE | IMPLEMENT_NOW | 1.8 | Per-task 'verified progress per token' metric logged to the flight recorder | The optimization target must be measured to be optimized. |
| 7.1 | OWNER | PROTOTYPE | 1.33 | SPRT stopping for retries | Sequential test on per-retry success; needs base rates. Start with simple 'no new evidence → stop'. |
| F7.7 | FABLE | PROTOTYPE | 1.33 | Session-limit awareness as a first-class signal (remaining budget from the provider) → shrink protocol depth ahead of the wall | Providers expose rate-limit headers; use them. |
| 7.3 | OWNER | PROTOTYPE | 0.67 | Failure-signature escalation to historically effective next step | recommended_recovery exists; connect to escalation ladder. |
| 7.10 | OWNER | PROTOTYPE | -0.33 | Post-escalation attribution and threshold recalibration | Log escalate→outcome pairs into P3. |
| 7.5 | OWNER | DEFER | -0.68 | CVaR escalation for catastrophic classes | Needs loss model; risk_class gating (4.9) covers the practical part. |
| 7.7 | OWNER | DEFER | -0.68 | Token auction across open tasks | Multi-task scheduling; no measured contention yet. |

## M8: Counterexample Generator

| id | src | decision | aid | summary | evidence |
|---|---|---|---|---|---|
| F8.4 | FABLE | IMPLEMENT_NOW | 16.8 | Sibling sweep: when a boundary bug is fixed in one component, run its counterexamples against all components sharing the boundary tag | F-004/F-010/F-017 were the same SSRF class in three places. |
| 8.9 | OWNER | IMPLEMENT_NOW | 11.2 | Cross-fix mutation (replay neighbours' counterexamples) | Retrieve adversarial_variants of similar VERIFIED cases and re-run — free from P2. |
| 8.10 | OWNER | IMPLEMENT_NOW | 11.2 | Counterexample→benchmark promotion after independent repro + dedup | Every SECREM repro test is already a benchmark; add the dedup/promotion rule to policy. |
| F8.5 | FABLE | IMPLEMENT_NOW | 11.2 | Counterexamples as first-class corpus entries with reproduction commands; retrieval by boundary | adversarial_variants field exists; add boundary tag index. |
| F8.6 | FABLE | IMPLEMENT_NOW | 11.2 | Must-stay-denied ratchet: negative tests are pinned (like KNOWN_SHELL_EXCEPTIONS) so weakening a check fails CI | Pattern exists in stage13 hostexec test; generalize. |
| F8.8 | FABLE | IMPLEMENT_NOW | 11.2 | Untrusted-content corpus: injection strings (SYSTEM:, ignore previous, ANSI, bidi) reused across MCP/browser/memory tests | Exists piecemeal in tests; centralize. |
| F8.10 | FABLE | IMPLEMENT_NOW | 11.2 | Counterexample credit: a variant that fails counts as a finding with its own learning record (FAILED_EXPERIMENT for the fix) | Closes the loop between attack and corpus. |
| 8.6 | OWNER | IMPLEMENT_NOW | 5.6 | Security-boundary mutation (paths/identities/scopes/symlinks/URLs/tool metadata) | This session's variants ARE this; codify as a shared mutator library used by SECREM tests. |
| F8.1 | FABLE | IMPLEMENT_NOW | 5.6 | Boundary mutator library: path (../, symlink, junction, encoded), identity (name re-register), URL (numeric IP forms, redirect), header (missing/garbage), approval (replay, tamper) | Every SECREM test in this session used these by hand; make them importable fixtures. |
| F8.2 | FABLE | IMPLEMENT_NOW | 5.6 | Invariant-derived counterexamples: for a predicate 'resolved(p) ∈ roots' auto-generate the 4 canonical violations | Predicate → violation catalog is finite and reusable. |
| 8.4 | OWNER | IMPLEMENT_NOW | 3.73 | Stateful sequence attacks (restart/replay/duplicate/out-of-order) | Replay guards exist; add a reusable sequence-mutator fixture for approvals/tool calls. |
| F8.9 | FABLE | IMPLEMENT_NOW | 3.73 | Sequence attacks over approvals: approve→re-register→resume; approve→tamper→resume; consumed→replay — as a generic harness for any approval-gated action | F-013/F-015 shape; reusable. |
| 8.1 | OWNER | PROTOTYPE | 2.67 | Property-based tests from typed invariants | hypothesis-style generation over ExpectedState/paths; good for security boundaries. |
| 8.3 | OWNER | PROTOTYPE | 2.67 | Boundary-value generator learned from historical bugs | Seed from learning corpus adversarial_variants field (already captured). |
| F8.3 | FABLE | PROTOTYPE | 2.67 | Fix-shaped attacks: given a diff, generate variants that target exactly the branch the fix added (e.g., 'what does the fix NOT check') | Uses diff + predicate list; strong for review. |
| F8.7 | FABLE | PROTOTYPE | 2.67 | Deterministic fuzz budget per boundary (N seeds, fixed RNG) run in CI; failures become NOT_REPRODUCIBLE tasks with the seed | Bounded, reproducible fuzzing. |
| 8.2 | OWNER | PROTOTYPE | 1.33 | Metamorphic variants | Same as 4.7 (dedup). |
| 8.7 | OWNER | DEFER | 1.07 | Failure minimizer (shrink counterexample) | Nice-to-have; PoCs are already minimal by construction. |
| 8.5 | OWNER | DEFER | 0.64 | Concurrency schedule perturbation | Hard to make deterministic; BUG-004 class bugs are loop-affinity, not races. |
| 8.8 | OWNER | DEFER | 0.64 | Coverage-novelty scoring of variants | Needs coverage instrumentation. |

## M9: Skill Factory

| id | src | decision | aid | summary | evidence |
|---|---|---|---|---|---|
| F9.1 | FABLE | IMPLEMENT_NOW | 8.4 | Skills are verification recipes first, generation recipes second: a skill must ship its ExpectedState template | Aligns skills with verifier-first. |
| F9.4 | FABLE | IMPLEMENT_NOW | 8.4 | Skill blast radius declared as allowed_paths/capabilities; runtime enforces (Deep Fix scope gate) | Skill cannot widen authority by construction. |
| F9.2 | FABLE | IMPLEMENT_NOW | 5.6 | Skill minting requires ≥3 VERIFIED cases sharing (bug_class, boundary) and one independent re-run | Concrete promotion rule. |
| F9.3 | FABLE | IMPLEMENT_NOW | 5.6 | Skills carry NEGATIVE examples (tempting wrong fix) as part of the contract | From F1.2; skills inherit it. |
| F9.8 | FABLE | IMPLEMENT_NOW | 5.6 | Skill = protocol fragment + probes + verification recipe, never a system-prompt blob | Composable with F2.1. |
| F9.10 | FABLE | IMPLEMENT_NOW | 5.6 | Skill provenance chain (cases → skill version → outcomes) in the corpus | Auditability; same store. |
| 9.8 | OWNER | IMPLEMENT_NOW | 4.6 | Negative-skill library (avoid rules) | teach_local_model 'Avoid:' lines + failed_experiments are exactly this; expose as a retrievable view. |
| 9.7 | OWNER | IMPLEMENT_NOW | 2.8 | Skill complexity penalty | Score = benefit − size; trivial rule in promotion. |
| F9.5 | FABLE | IMPLEMENT_NOW | 2.8 | Skill A/B is Direct vs Skill with the SAME model on hidden holdout; promotion needs VerifiedSuccess +Δ and no security regression | Owner's Stage 3 made concrete. |
| F9.6 | FABLE | IMPLEMENT_NOW | 2.8 | Skill retirement when its cases are superseded (F1.7) | Lifecycle symmetry. |
| F9.9 | FABLE | IMPLEMENT_NOW | 2.8 | Model-specific skill variants only when measured (F9.5) — otherwise one canonical skill | Avoids N×M skill explosion. |
| 9.4 | OWNER | IMPLEMENT_NOW | 1.87 | Hidden-holdout contamination scanner | learning_guard has holdout filtering (runner_holdout_exclusion); extend scanner to skill training examples. |
| 9.5 | OWNER | IMPLEMENT_NOW | 1.87 | Shadow deployment without new authority | Consistent with Learning Guard; skills run shadow first. |
| 9.10 | OWNER | IMPLEMENT_NOW | 1.87 | Automatic rollback + version pinning on regression | Learning Guard has current_version_id; wire rollback on VerifiedSuccess drop. |
| F9.7 | FABLE | PROTOTYPE | 1.33 | Skill size budget in tokens with a measured cache-hit requirement (stable prefix) | Keeps skills cheap at runtime. |
| 9.2 | OWNER | IMPLEMENT_NOW | 0.93 | Skill contracts (inputs/outputs/capabilities/effects/evidence/rollback) | Matches existing capability metadata (V2.6); make contracts mandatory for new skills. |
| 9.1 | OWNER | PROTOTYPE | 0.33 | Skill candidates from clusters of VERIFIED cases | Corpus small; clustering by bug_class tag is the first cut. |
| 9.6 | OWNER | DEFER | 0.27 | Skill decay/drift monitoring | Needs volume. |
| 9.3 | OWNER | DEFER | 0.16 | Composability checker (preconditions/effects) | Few skills exist. |
| 9.9 | OWNER | DEFER | -0.68 | Per-model skill compatibility scores | Needs P3. |

## M10: Self-Improvement Laboratory

| id | src | decision | aid | summary | evidence |
|---|---|---|---|---|---|
| F10.1 | FABLE | IMPLEMENT_NOW | 16.8 | Improvement claims are learning records with status; nothing is 'promoted' outside the corpus lifecycle | One lifecycle for fixes, skills, protocols, routes. |
| F10.4 | FABLE | IMPLEMENT_NOW | 16.8 | Security gate is a veto, not a score term: any must-deny test failing blocks promotion regardless of VerifiedSuccess gain | Non-negotiable per owner; encode as code. |
| F10.10 | FABLE | IMPLEMENT_NOW | 16.8 | No self-modifying prompts in production paths: prompt changes are candidates in the lab with the same lifecycle | Closes the 'better because it says so' loop. |
| 10.4 | OWNER | IMPLEMENT_NOW | 11.2 | Canary/shadow rollout | Learning Guard already does shadow→verified→owner promotion. |
| 10.5 | OWNER | IMPLEMENT_NOW | 11.2 | Automatic rollback thresholds | Dedup with 9.10. |
| F10.5 | FABLE | IMPLEMENT_NOW | 11.2 | Experiment registry rows are learning records too (status=UNVERIFIED until independent re-run) | Reuse validator/store. |
| F10.7 | FABLE | IMPLEMENT_NOW | 11.2 | Rollback is a corpus operation: mark the promoted record REJECTED with evidence; retrieval instantly stops preferring it | Rollback without deploys. |
| F10.3 | FABLE | IMPLEMENT_NOW | 10.2 | Minimum evidence for promotion is task-count-based with an honest power note (n<20 → 'insufficient evidence', never 'improved') | Replaces fake statistics with honesty. |
| 10.6 | OWNER | IMPLEMENT_NOW | 5.6 | Adversarial holdout generated independently | SECREM variants written by a different agent than the fixer — codify: holdout author ≠ candidate author. |
| F10.2 | FABLE | IMPLEMENT_NOW | 5.6 | Holdout is a git-tracked set of (test id, expected verdict) with hashes; candidates run in a workspace where holdout files are absent | Contamination-proof by construction. |
| F10.6 | FABLE | PROTOTYPE | 4.0 | Ablation-by-gate: each Deep Fix gate can be disabled in the lab only; production always full — lab measures which gate buys VerifiedSuccess | Answers 'ceremonial or not' with data. |
| 10.1 | OWNER | IMPLEMENT_NOW | 3.73 | Immutable experiment registry (candidate/baseline/dataset/SHA/metrics) | learning corpus + git SHA already immutable-ish; add experiments.jsonl with the same validator. |
| 10.7 | OWNER | IMPLEMENT_NOW | 3.73 | Evaluation contamination detector | Track file hashes of holdout; refuse promotion if candidate context contained them. |
| 10.3 | OWNER | PROTOTYPE | 3.0 | Multiple-comparison correction | Same as 10.2 (dedup). |
| F10.8 | FABLE | PROTOTYPE | 2.67 | Owner promotion UI shows the evidence ledger diff (before/after) rather than a summary | Human decision on evidence, not prose. |
| 10.9 | OWNER | PROTOTYPE | 2.4 | Mechanism ablation tests | Deep Fix gates can be toggled individually; ablation harness measures VerifiedSuccess per gate. |
| F10.9 | FABLE | DEFER | 2.13 | Longitudinal re-verification is a scheduled task with the same verifier; drift = a new FAILED_EXPERIMENT record | Needs scheduler wiring; design is trivial. |
| 10.2 | OWNER | PROTOTYPE | 1.67 | Sequential statistical testing with FDR control | At current volumes any test is underpowered; implement the stopping rule but report power honestly. |
| 10.8 | OWNER | DEFER | 1.07 | Pareto frontier archive | Only after multiple candidates exist. |
| 10.10 | OWNER | DEFER | 1.07 | Longitudinal drift re-evaluation | Needs time series. |
