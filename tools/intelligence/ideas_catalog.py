"""Каталог идей интеллекта Bossman: 100 owner + 100 Fable, с решениями.

Источник owner-идей — INTELLIGENCE_EXPANSION_100_PLUS_FABLE_100.md (пак V2).
Каждая запись — явное инженерное решение (без скрытой цепочки рассуждений):
IMPLEMENT_NOW / PROTOTYPE / DEFER / REJECT + оценка выгоды/стоимости/рисков.
Генерирует data/learning/intelligence_ideas.jsonl и docs/learning/intelligence_ideas/INDEX.md.

Оценки — экспертные (label: ESTIMATE), не измерения; где есть измерение —
указано в BASELINE/BENCHMARK. EVI = ΔVerifiedSuccess×Confidence×Reuse×Coverage
считается по порядковым шкалам (0..3) как ранжирующий aid, не как истина.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_JSONL = ROOT / "data" / "learning" / "intelligence_ideas.jsonl"
OUT_MD = ROOT / "docs" / "learning" / "intelligence_ideas" / "INDEX.md"

MECHANISMS = {
    1: "Experience Retrieval Engine", 2: "Reasoning Compiler", 3: "Hypothesis Tournament",
    4: "Verifier-First Development", 5: "Automatic Context Compiler", 6: "Dynamic Model Router",
    7: "Adaptive Compute / Escalation", 8: "Counterexample Generator", 9: "Skill Factory",
    10: "Self-Improvement Laboratory",
}

# Общие примитивы, к которым сводятся многие идеи (дедупликация по примитиву).
PRIMITIVES = {
    "P1": "Evidence ledger: типизированные наблюдения с provenance/TTL/hash (есть: bcc/v2/verification, flight_recorder)",
    "P2": "Learning corpus + retrieval с фильтрами и статусами (есть: learning/trace.py)",
    "P3": "Per-(model,task_class,failure_pattern) outcome table (частично: router class stats, failure_patterns)",
    "P4": "Budgeted decision loop: expected-gain vs cost с ранней остановкой (частично: uncertainty, adaptive compute)",
    "P5": "Hidden holdout + promotion pipeline с независимой верификацией (есть: learning_guard)",
    "P6": "Deterministic mutators/counterexample generators над типизированными инвариантами (частично: redteam PoCs, variants)",
    "P7": "Context slots с бюджетами и attribution (частично: bossman/context.py budget shares)",
}

# (id, summary, decision, rationale, benefit, cost, sec_risk, goodhart, overlaps/primitive, deps)
# Шкалы: benefit/cost 0..3; risk L/M/H.
OWNER = [
 # M1
 ("1.1","Hybrid retrieval: embeddings + BM25 + code-path/symbol similarity","PROTOTYPE","BM25+symbol match cheap and offline; embeddings already in context_engine. Value only measurable once corpus > ~50 cases.",2,1,"L","L","P2",["corpus>=50"]),
 ("1.2","MMR/diversity selection across failure modes","DEFER","Corpus is 16 cases; diversity is moot. Reconsider at >100.",1,1,"L","L","P2",[]),
 ("1.3","Temporal decay with timeless-invariant exception","IMPLEMENT_NOW","Cheap: tag lessons as invariant vs implementation-detail; decay only the latter. Prevents stale code paths from being retrieved as truth.",2,0,"L","L","P2",[]),
 ("1.4","Negative retrieval of counterexamples/FAILED_EXPERIMENT","IMPLEMENT_NOW","Already supported (include_failed + retrieval_warning). Make it default for security bug classes.",2,0,"L","M","P2",[]),
 ("1.5","Query decomposition by symptom/component/invariant then fuse","PROTOTYPE","Filters exist (tags); fusion needs scoring. Prototype as weighted union.",1,1,"L","L","P2",[]),
 ("1.6","Token-aware compression with progressive disclosure","IMPLEMENT_NOW","compact() exists; add depth-2 (evidence) on demand. Directly serves token economy.",2,0,"L","L","P7",[]),
 ("1.7","Retrieval confidence gate (inject nothing when weak)","IMPLEMENT_NOW","Misleading context is worse than none; threshold on tag-match count is trivial.",2,0,"L","L","P2",[]),
 ("1.8","Cross-model usefulness scoring per case","DEFER","Needs P3 outcome table populated by real runs; no data yet.",2,2,"L","M","P3",["P3"]),
 ("1.9","Case lineage/versioning tied to code architecture","IMPLEMENT_NOW","Store end_sha + code paths (done); invalidate when paths vanish — a cheap staleness check.",2,0,"L","L","P2",[]),
 ("1.10","Retrieval outcome feedback updating ranking","DEFER","Requires attribution data (P3); Goodhart risk if cases 'win' by being injected often.",2,2,"L","H","P3",["P3"]),
 # M2
 ("2.1","Composable micro-protocols instead of monolithic workflows","IMPLEMENT_NOW","Deep Fix gates are already modular states; expose them as protocol fragments.",2,1,"L","L","-",[]),
 ("2.2","Difficulty estimator selects protocol depth","IMPLEMENT_NOW","classify_reasoning L0–L4 exists (V2.6 B); wire it to choose Deep Fix vs direct.",2,0,"L","M","P4",[]),
 ("2.3","Early-exit on decisive evidence","IMPLEMENT_NOW","Evidence ledger + stop rule; falls out of P4.",2,0,"L","L","P4",[]),
 ("2.4","Protocol switching on contradiction","PROTOTYPE","Needs contradiction detector (uncertainty.contradiction exists as scalar).",1,1,"L","M","P4",[]),
 ("2.5","Constraint compiler → machine-checkable gates","IMPLEMENT_NOW","This is exactly what Deep Fix gates and verification ExpectedState are. Extend with owner invariants list.",3,1,"L","L","P1",[]),
 ("2.6","Plan linting (missing verification, cycles, unauthorized effects)","IMPLEMENT_NOW","CompiledTask.ordered() detects cycles; add 'every mutating step has verification' lint.",2,0,"L","L","P1",[]),
 ("2.7","Domain adapters (coding/security/research/ops/data/business)","DEFER","Premature; one adapter (coding/security) is enough until measured.",1,2,"L","L","-",[]),
 ("2.8","Protocol outcome telemetry (ceremonial-step detection)","PROTOTYPE","Record per-state duration/tokens in Deep Fix history; analyze later.",2,1,"L","M","P3",[]),
 ("2.9","Protocol mutation sandbox on hidden evals","DEFER","Self-improvement lab prerequisite (P5) and evals corpus needed.",2,3,"M","H","P5",["P5"]),
 ("2.10","Failure-specific recovery protocols from verified signatures","PROTOTYPE","failure_patterns.recommended_recovery exists; link to Deep Fix hypotheses seed.",2,1,"L","L","P3",[]),
 # M3
 ("3.1","Evidence-disagreement generator (discriminating tests)","IMPLEMENT_NOW","Core of hypothesis tournament: ask 'which observation splits H1/H2' — make it a required field before patch.",3,1,"L","L","P1",[]),
 ("3.2","Bayesian posterior calibrated from historical root-cause frequencies","DEFER","No frequency table yet; prose priors would be fake precision.",2,2,"L","M","P3",["P3"]),
 ("3.3","Cheap-test-first scheduling by info gain per token","IMPLEMENT_NOW","Order discriminating tests by (cost, expected split) — heuristic is enough.",2,0,"L","L","P4",[]),
 ("3.4","Contrarian hypothesis slot","IMPLEMENT_NOW","Zero cost: require one structurally different hypothesis in root_cause_hypotheses.",1,0,"L","L","-",[]),
 ("3.5","Dependency-aware hypotheses (root vs symptom)","PROTOTYPE","Model as parent links between hypotheses; benefit unmeasured.",1,1,"L","L","-",[]),
 ("3.6","Automatic hypothesis merging","REJECT","Semantic-equivalence detection by LLM adds tokens for little gain at ≤5 hypotheses.",0,1,"L","L","-",[]),
 ("3.7","Stop rule on posterior margin + verification sufficiency","IMPLEMENT_NOW","Stop when one hypothesis survives all discriminating tests; no fixed K.",2,0,"L","L","P4",[]),
 ("3.8","Historical prior conditioning by component/error class","DEFER","Needs P3.",2,2,"L","M","P3",["P3"]),
 ("3.9","Multi-agent hypothesis generation gated by expected diversity","DEFER","Cost high; evidence of diversity benefit absent. Try after single-agent baseline.",1,2,"L","M","P4",[]),
 ("3.10","Postmortem calibration of predicted probabilities","PROTOTYPE","Log predicted top hypothesis vs verified root cause in learning record (field exists) — analysis later.",2,0,"L","L","P2",[]),
 # M4
 ("4.1","Verification plan hashed/bound to the task before patch","IMPLEMENT_NOW","Binds expected state before coding (anti goalpost); reuse approval_digest style hash.",3,1,"L","L","P1",[]),
 ("4.2","Evidence hierarchy (observation > independent impl > model judgment)","IMPLEMENT_NOW","Already encoded in F-012 fix (text can veto, never approve); document as policy levels.",3,0,"L","L","P1",[]),
 ("4.3","Two-channel verification for high-risk actions","PROTOTYPE","For irreversible effects: require two independent observers (e.g., file hash + DB row).",2,1,"L","L","P1",[]),
 ("4.4","Freshness TTL on observations","IMPLEMENT_NOW","observed_at exists; add max-age check in verify_all.",2,0,"L","L","P1",[]),
 ("4.5","Anti-echo verifier","IMPLEMENT_NOW","Done for review gate (F-012). Extend to Deep Fix: evidence derived from coder text is not evidence.",3,0,"L","L","P1",[]),
 ("4.6","Negative-control tests that must stay failing/denied","IMPLEMENT_NOW","Security fixes ship with must-deny tests already; make it a Deep Fix gate for security bug classes.",2,0,"L","L","P6",[]),
 ("4.7","Metamorphic verification","PROTOTYPE","Useful for non-exact outputs (research/media); needs per-domain relations.",1,2,"L","L","P6",[]),
 ("4.8","Verifier calibration dataset (FP/FN by verifier type)","DEFER","Needs volume; log verdict+later outcome first.",2,2,"L","M","P3",[]),
 ("4.9","Risk-adaptive verification budget","IMPLEMENT_NOW","risk_class already on CompiledTask; map to required evidence count.",2,0,"L","L","P4",[]),
 ("4.10","Verification provenance graph","PROTOTYPE","Evidence ledger links expected→action→observed; graph view is a rendering of P1.",1,1,"L","L","P1",[]),
 # M5
 ("5.1","AST/symbol/call-graph slicing","PROTOTYPE","code_index exists (BM25 over symbols); slicing by call graph is a real token saver but needs tree-sitter or ast per language.",2,2,"L","L","P7",[]),
 ("5.2","Change-impact context from recent commits/diff","IMPLEMENT_NOW","git diff/log is free; prioritize touched symbols. Big win for fix tasks.",3,0,"L","L","P7",[]),
 ("5.3","Staleness invalidation on HEAD/tool observation change","IMPLEMENT_NOW","Tie context cache key to HEAD sha + observation ids.",2,0,"L","L","P7",[]),
 ("5.4","Redundancy clustering","DEFER","Marginal at current context sizes; measure first.",1,1,"L","L","P7",[]),
 ("5.5","Progressive context expansion","IMPLEMENT_NOW","Start with targeted slice; expand only when verifier/uncertainty demands (P4).",3,1,"L","L","P4",[]),
 ("5.6","Context slots with explicit budgets","IMPLEMENT_NOW","BLOCK_SHARES already exist in core context.py; add 'evidence' and 'experience' slots.",2,0,"L","L","P7",[]),
 ("5.7","Loss-aware compression test","PROTOTYPE","Check that compressed context still answers invariant questions — needs an eval harness.",2,2,"L","M","P5",[]),
 ("5.8","Context contamination detector","PROTOTYPE","Flag docs older than code paths they describe; unverified memories already tagged (F-006).",2,1,"L","L","P1",[]),
 ("5.9","Per-model context profiles","DEFER","Needs P3 data.",1,2,"L","M","P3",["P3"]),
 ("5.10","Context attribution telemetry","DEFER","Attribution is expensive to estimate honestly; ablation-based only in the lab.",2,3,"L","H","P5",["P5"]),
 # M6
 ("6.1","Bayesian competence posteriors per task class","PROTOTYPE","Beta posterior over per-(model,class) success is cheap; router class stats exist (n>=5 rule). Extend with intervals.",2,1,"L","M","P3",[]),
 ("6.2","Contextual bandit exploration on shadow tasks only","DEFER","Requires shadow lane + volume.",2,2,"M","M","P5",["P5"]),
 ("6.3","Drift detector for model/provider changes","IMPLEMENT_NOW","Key stats by model version/date; decay old evidence — small change to class stats.",2,1,"L","L","P3",[]),
 ("6.4","Failure-mode router (known weaknesses)","PROTOTYPE","Route away from models with high rate of a failure pattern; depends on P3 population.",2,1,"L","M","P3",[]),
 ("6.5","Ensemble escalation when disagreement resolution worth cost","DEFER","Cost model needed first.",1,2,"L","M","P4",[]),
 ("6.6","Model+protocol joint routing","PROTOTYPE","Choose (model, Deep Fix depth) jointly; natural once 2.2 lands.",2,1,"L","M","P4",[]),
 ("6.7","Model+context-budget joint optimization","PROTOTYPE","Real window (real_window) exists; joint choice is an ordering rule.",2,1,"L","L","P7",[]),
 ("6.8","Deadline-aware routing","DEFER","No deadline signal in tasks today.",1,1,"L","L","-",[]),
 ("6.9","Resource-aware local scheduling (VRAM residency)","IMPLEMENT_NOW","Model swaps dominate local latency; Resource Brain lease exists — prefer resident model when scores tie.",3,1,"L","L","-",[]),
 ("6.10","Provider reliability prior (outages/429/schema compliance)","IMPLEMENT_NOW","Circuit breaker + 429 counters exist; fold into candidate score.",2,0,"L","L","P3",[]),
 # M7
 ("7.1","SPRT stopping for retries","PROTOTYPE","Sequential test on per-retry success; needs base rates. Start with simple 'no new evidence → stop'.",2,1,"L","L","P4",[]),
 ("7.2","Retry diversity requirement","IMPLEMENT_NOW","Governor already stops identical-error loops; require changed strategy signature per retry.",2,0,"L","L","P4",[]),
 ("7.3","Failure-signature escalation to historically effective next step","PROTOTYPE","recommended_recovery exists; connect to escalation ladder.",2,1,"L","L","P3",[]),
 ("7.4","Budget reservation for verification","IMPLEMENT_NOW","Reserve a fixed share of task budget for verify/commit; trivial and high value against 'spent it all generating'.",3,0,"L","L","P4",[]),
 ("7.5","CVaR escalation for catastrophic classes","DEFER","Needs loss model; risk_class gating (4.9) covers the practical part.",1,2,"L","M","P4",[]),
 ("7.6","Parallel cheap probes before expensive escalation","IMPLEMENT_NOW","Run discriminating cheap tests concurrently (they are deterministic) before a strong-model call.",2,1,"L","L","P4",[]),
 ("7.7","Token auction across open tasks","DEFER","Multi-task scheduling; no measured contention yet.",1,2,"L","M","P4",[]),
 ("7.8","Deadline checkpoint policy (stop early to verify/commit/push)","IMPLEMENT_NOW","This session's own lesson: reserve time for checkpoint; encode as a gate in Deep Fix/agent templates.",3,0,"L","L","P4",[]),
 ("7.9","Cloud escalation requires predicted gain + policy approval","IMPLEMENT_NOW","Policy side exists (fail-closed); add gain threshold from uncertainty before requesting approval.",2,0,"L","L","P4",[]),
 ("7.10","Post-escalation attribution and threshold recalibration","PROTOTYPE","Log escalate→outcome pairs into P3.",2,1,"L","M","P3",[]),
 # M8
 ("8.1","Property-based tests from typed invariants","PROTOTYPE","hypothesis-style generation over ExpectedState/paths; good for security boundaries.",2,1,"L","L","P6",[]),
 ("8.2","Metamorphic variants","PROTOTYPE","Same as 4.7 (dedup).",1,1,"L","L","P6",[]),
 ("8.3","Boundary-value generator learned from historical bugs","PROTOTYPE","Seed from learning corpus adversarial_variants field (already captured).",2,1,"L","L","P6",[]),
 ("8.4","Stateful sequence attacks (restart/replay/duplicate/out-of-order)","IMPLEMENT_NOW","Replay guards exist; add a reusable sequence-mutator fixture for approvals/tool calls.",2,1,"L","L","P6",[]),
 ("8.5","Concurrency schedule perturbation","DEFER","Hard to make deterministic; BUG-004 class bugs are loop-affinity, not races.",1,2,"L","L","P6",[]),
 ("8.6","Security-boundary mutation (paths/identities/scopes/symlinks/URLs/tool metadata)","IMPLEMENT_NOW","This session's variants ARE this; codify as a shared mutator library used by SECREM tests.",3,1,"L","L","P6",[]),
 ("8.7","Failure minimizer (shrink counterexample)","DEFER","Nice-to-have; PoCs are already minimal by construction.",1,1,"L","L","P6",[]),
 ("8.8","Coverage-novelty scoring of variants","DEFER","Needs coverage instrumentation.",1,2,"L","L","P6",[]),
 ("8.9","Cross-fix mutation (replay neighbours' counterexamples)","IMPLEMENT_NOW","Retrieve adversarial_variants of similar VERIFIED cases and re-run — free from P2.",2,0,"L","L","P2",[]),
 ("8.10","Counterexample→benchmark promotion after independent repro + dedup","IMPLEMENT_NOW","Every SECREM repro test is already a benchmark; add the dedup/promotion rule to policy.",2,0,"L","L","P5",[]),
 # M9
 ("9.1","Skill candidates from clusters of VERIFIED cases","PROTOTYPE","Corpus small; clustering by bug_class tag is the first cut.",2,1,"L","M","P2",[]),
 ("9.2","Skill contracts (inputs/outputs/capabilities/effects/evidence/rollback)","IMPLEMENT_NOW","Matches existing capability metadata (V2.6); make contracts mandatory for new skills.",2,1,"L","L","-",[]),
 ("9.3","Composability checker (preconditions/effects)","DEFER","Few skills exist.",1,2,"L","L","-",[]),
 ("9.4","Hidden-holdout contamination scanner","IMPLEMENT_NOW","learning_guard has holdout filtering (runner_holdout_exclusion); extend scanner to skill training examples.",2,1,"L","L","P5",[]),
 ("9.5","Shadow deployment without new authority","IMPLEMENT_NOW","Consistent with Learning Guard; skills run shadow first.",2,1,"L","L","P5",[]),
 ("9.6","Skill decay/drift monitoring","DEFER","Needs volume.",1,1,"L","L","P3",[]),
 ("9.7","Skill complexity penalty","IMPLEMENT_NOW","Score = benefit − size; trivial rule in promotion.",1,0,"L","L","P5",[]),
 ("9.8","Negative-skill library (avoid rules)","IMPLEMENT_NOW","teach_local_model 'Avoid:' lines + failed_experiments are exactly this; expose as a retrievable view.",2,0,"L","M","P2",[]),
 ("9.9","Per-model skill compatibility scores","DEFER","Needs P3.",2,2,"L","M","P3",["P3"]),
 ("9.10","Automatic rollback + version pinning on regression","IMPLEMENT_NOW","Learning Guard has current_version_id; wire rollback on VerifiedSuccess drop.",2,1,"L","L","P5",[]),
 # M10
 ("10.1","Immutable experiment registry (candidate/baseline/dataset/SHA/metrics)","IMPLEMENT_NOW","learning corpus + git SHA already immutable-ish; add experiments.jsonl with the same validator.",2,1,"L","L","P5",[]),
 ("10.2","Sequential statistical testing with FDR control","PROTOTYPE","At current volumes any test is underpowered; implement the stopping rule but report power honestly.",2,1,"L","M","P5",[]),
 ("10.3","Multiple-comparison correction","PROTOTYPE","Same as 10.2 (dedup).",1,0,"L","M","P5",[]),
 ("10.4","Canary/shadow rollout","IMPLEMENT_NOW","Learning Guard already does shadow→verified→owner promotion.",2,0,"L","L","P5",[]),
 ("10.5","Automatic rollback thresholds","IMPLEMENT_NOW","Dedup with 9.10.",2,0,"L","L","P5",[]),
 ("10.6","Adversarial holdout generated independently","IMPLEMENT_NOW","SECREM variants written by a different agent than the fixer — codify: holdout author ≠ candidate author.",3,1,"L","L","P5",[]),
 ("10.7","Evaluation contamination detector","IMPLEMENT_NOW","Track file hashes of holdout; refuse promotion if candidate context contained them.",2,1,"L","L","P5",[]),
 ("10.8","Pareto frontier archive","DEFER","Only after multiple candidates exist.",1,1,"L","L","P5",[]),
 ("10.9","Mechanism ablation tests","PROTOTYPE","Deep Fix gates can be toggled individually; ablation harness measures VerifiedSuccess per gate.",3,2,"L","L","P5",[]),
 ("10.10","Longitudinal drift re-evaluation","DEFER","Needs time series.",1,1,"L","L","P5",[]),
]

FABLE = [
 # M1 — retrieval
 ("F1.1","Retrieval by INVARIANT VIOLATION signature, not by text: index cases by (boundary, violated predicate) e.g. 'resolved-path ∉ roots'","IMPLEMENT_NOW","Security bugs recur as the same predicate violation across components; symptom text differs. Cheap: predicates are already in generalizable_lessons.",3,0,"L","L","P2",[]),
 ("F1.2","Counterfactual-pair retrieval: return the case AND the tempting wrong fix that was rejected, as a pair","IMPLEMENT_NOW","Local models copy the first plausible fix; showing the rejected alternative with its evidence prevents the common failure mode. Fields exist.",3,0,"L","L","P2",[]),
 ("F1.3","Retrieval budget as function of uncertainty: 0 cases at L0, up to 8 at L4","IMPLEMENT_NOW","Ties retrieval cost to classify_reasoning level; avoids paying context for trivial tasks.",2,0,"L","L","P4",[]),
 ("F1.4","Evidence-first ordering: put the decisive observation before the narrative in the injected snippet","IMPLEMENT_NOW","Attention is front-loaded; the decisive test is what the model must reproduce. Zero cost (render order).",2,0,"L","L","P7",[]),
 ("F1.5","Provenance-signed cases: case_id + end_sha + verifier in every injected snippet so the model can cite, not paraphrase","IMPLEMENT_NOW","Prevents 'memory' being upgraded to authority; matches F-006 marker policy.",2,0,"L","L","P2",[]),
 ("F1.6","Retrieval dry-run audit: log which cases were injected per run without acting on them, to build the P3 usefulness table for free","PROTOTYPE","Turns 1.8/1.10 from 'needs data' into 'collecting data' at zero behaviour risk.",2,1,"L","L","P3",[]),
 ("F1.7","Anti-retrieval list: cases whose lessons were later invalidated by a VERIFIED case get a 'superseded_by' link and are never injected","IMPLEMENT_NOW","Lineage (1.9) done right: supersession is explicit, not decay-by-time.",2,0,"L","L","P2",[]),
 ("F1.8","Tool-output-keyed retrieval: trigger on error signatures from fresh tool output (classify_error) rather than on the task prompt","IMPLEMENT_NOW","The failure pattern is known only after the first test run; retrieving at that moment is where cases help.",3,1,"L","L","P3",[]),
 ("F1.9","Cross-app case bridging: tag whether a lesson transfers bossman-core↔command-center (two apps, no shared code)","IMPLEMENT_NOW","This repo's specific structure; the same SSRF fix was needed three times (net.py, browser, discovery).",2,0,"L","L","P2",[]),
 ("F1.10","Retrieval regression test: fixed queries must return fixed case ids (golden set) so store changes can't silently degrade retrieval","IMPLEMENT_NOW","Retrieval is code; test it like code.",2,0,"L","L","P2",[]),
 # M2 — reasoning compiler
 ("F2.1","Gate-as-code, not gate-as-prompt: every protocol step is a Python predicate over the evidence ledger (Deep Fix pattern) — prompts only explain","IMPLEMENT_NOW","A model can skip a prompted step; it cannot skip a state machine. Already the Deep Fix design; generalize.",3,1,"L","L","P1",[]),
 ("F2.2","Minimal-sufficient-protocol search: start from the 3-step protocol and add a step only when a past FAILED_EXPERIMENT of this class shows it was needed","PROTOTYPE","Directly attacks ceremonial token waste with negative knowledge.",2,1,"L","M","P2",[]),
 ("F2.3","Refusal-as-first-class output: protocol may end in NOT_REPRODUCIBLE/BLOCKED_ENV with a machine-readable next action instead of a fabricated fix","IMPLEMENT_NOW","Prevents false-success; Deep Fix already has failure states — make every protocol have them.",3,0,"L","L","P1",[]),
 ("F2.4","Protocol replay from the flight recorder: re-run a past protocol against current HEAD to detect regressions of the fix itself","PROTOTYPE","Turns recorded protocols into regression tests.",2,2,"L","L","P1",[]),
 ("F2.5","Two-phase commit for plans: 'declare expected effects' → 'execute' → 'diff observed vs declared' (unexpected effects = failure even if tests pass)","IMPLEMENT_NOW","Catches broad diffs and side effects; observed/declared diff is the strongest anti-drift check.",3,1,"L","L","P1",[]),
 ("F2.6","Compile owner invariants into ExpectedState templates once; protocols reference them by id","IMPLEMENT_NOW","Owner constraints become data (verification.py ExpectedState), reused across tasks.",2,1,"L","L","P1",[]),
 ("F2.7","Protocol cost preview: before starting, print (states, expected tokens, expected tool calls) and require it to fit the reservation","IMPLEMENT_NOW","Budget awareness at plan time; pairs with 7.4.",2,0,"L","L","P4",[]),
 ("F2.8","Deterministic scaffolding steps run without a model (repo map, test discovery, diff stat) — the model sees results only","IMPLEMENT_NOW","Largest token saver: discovery is mechanical. This session's cost was discovery.",3,1,"L","L","P7",[]),
 ("F2.9","Protocol signatures in learning records (which states ran, in what order) for later ablation","IMPLEMENT_NOW","Free telemetry (Deep Fix history) — enables 10.9.",2,0,"L","L","P3",[]),
 ("F2.10","Human-checkpoint scheduling as protocol step: OWNER_DECISION_REQUIRED emitted with a bounded question set","IMPLEMENT_NOW","Avoids blocking loops on unanswerable questions; state exists in Deep Fix.",2,0,"L","L","-",[]),
 # M3 — hypothesis tournament
 ("F3.1","Hypothesis must name its DISCRIMINATING OBSERVATION up front; hypotheses without one are not admitted","IMPLEMENT_NOW","Forces falsifiability; cheapest anti-anchoring device.",3,0,"L","L","P1",[]),
 ("F3.2","Tournament by elimination with cached observations: each test result is a fact reused across all hypotheses (no re-running)","IMPLEMENT_NOW","Evidence ledger dedups tests; cuts tool calls.",2,0,"L","L","P1",[]),
 ("F3.3","'Wrong-layer' detector: if all hypotheses live in the same file/layer, force one hypothesis one layer up/down","PROTOTYPE","Empirically root causes cross layers (F-005 was 'a second executor path', not the quoting).",2,0,"L","L","-",[]),
 ("F3.4","Hypothesis debt: unresolved hypotheses are stored as open questions in the learning record instead of being dropped","IMPLEMENT_NOW","Negative knowledge and follow-ups become retrievable.",2,0,"L","L","P2",[]),
 ("F3.5","Cheap-probe library per failure class (e.g., loop-affinity → 'run twice under asyncio.run')","PROTOTYPE","Turns failure_patterns into executable probes.",2,1,"L","L","P6",[]),
 ("F3.6","Symmetric-evidence rule: an observation that is consistent with all hypotheses has zero weight and is not recorded as support","IMPLEMENT_NOW","Prevents confirmation-by-volume in prose.",2,0,"L","L","P1",[]),
 ("F3.7","Root-cause acceptance requires reproducing the bug FROM the cause (forward reproduction), not just explaining it","IMPLEMENT_NOW","Strongest test of a causal claim; Deep Fix gate extension.",3,1,"L","L","P1",[]),
 ("F3.8","Bounded tournament: max 5 hypotheses, max 3 rounds; overflow → OWNER_DECISION_REQUIRED with the ledger","IMPLEMENT_NOW","Bounds tokens and time deterministically.",2,0,"L","L","P4",[]),
 ("F3.9","Calibration by construction: record P(top hypothesis) as ordinal (likely/possible/unlikely) and score Brier over time","PROTOTYPE","Ordinal avoids fake precision; enables 3.10.",1,0,"L","L","P3",[]),
 ("F3.10","Tournament transcripts are learning material only after verification (else FAILED_EXPERIMENT)","IMPLEMENT_NOW","Consistent with corpus policy.",2,0,"L","L","P2",[]),
 # M4 — verifier-first
 ("F4.1","Verification plan is written as ExpectedState JSON and stored BEFORE the patch (hash in the run); patch cannot change it","IMPLEMENT_NOW","Concrete form of 4.1 using existing verification.py types.",3,1,"L","L","P1",[]),
 ("F4.2","Verifier isolation: the verifier process gets only the plan + read tools, never the coder's transcript","IMPLEMENT_NOW","Removes echo channel entirely (stronger than anti-echo filtering).",3,1,"L","L","P1",[]),
 ("F4.3","Negative controls auto-derived: for every 'must allow' expectation generate the mirrored 'must deny' case from the same policy","PROTOTYPE","Halves the cost of writing security tests; mutator over ExpectedState.",2,1,"L","L","P6",[]),
 ("F4.4","Observation freshness proven by nonce: verifier writes a nonce into the environment and expects it in the observation path (e.g., temp file) to detect cached observers","PROTOTYPE","Detects stale/cached observation sources.",1,1,"L","L","P1",[]),
 ("F4.5","Evidence sufficiency by risk class: irreversible → 2 independent observation kinds; sensitive → 1 observation + negative control; normal → 1 observation","IMPLEMENT_NOW","Deterministic rule from CompiledTask.risk_class.",2,0,"L","L","P4",[]),
 ("F4.6","Verification results are events on the bus (evaluation.completed exists) with the evidence hash so dashboards and Learning Guard consume the same fact","IMPLEMENT_NOW","One source of truth for 'verified'.",2,0,"L","L","P1",[]),
 ("F4.7","'Effect diff' verification: snapshot of declared-writable paths before/after; any change outside the declared set fails verification","IMPLEMENT_NOW","Catches collateral writes; pairs with F2.5.",3,1,"L","L","P1",[]),
 ("F4.8","Verifier cannot be the same model FAMILY as the coder when the effect is not observable (judgment-only) — else result is UNVERIFIED","IMPLEMENT_NOW","Correlated errors; encode as policy in Deep Fix verifier check.",2,0,"L","L","P5",[]),
 ("F4.9","Time-boxed verification with explicit UNVERIFIED on timeout (never PASS by timeout)","IMPLEMENT_NOW","Fail-closed default for verification.",2,0,"L","L","P1",[]),
 ("F4.10","Verification recipes stored per bug class in the corpus ('Verify using:') and retrieved before patching","IMPLEMENT_NOW","Field already captured; retrieval hook is trivial.",2,0,"L","L","P2",[]),
 # M5 — context compiler
 ("F5.1","Failing-test-first context: the failing test + the code it imports (transitively, depth 2) is the default context; nothing else until asked","IMPLEMENT_NOW","Most fix tasks resolve within this slice; measured in this session by how little of the repo mattered per finding.",3,1,"L","L","P7",[]),
 ("F5.2","Context as a manifest with hashes: the model receives file@sha slices; stale slices are refused at tool time","IMPLEMENT_NOW","Prevents editing from stale reads (a classic local-model failure).",2,1,"L","L","P7",[]),
 ("F5.3","Symbol-level rather than file-level slices via existing code_index; include only referenced symbols","PROTOTYPE","Bigger saver than file slices; index already has symbols.",3,2,"L","L","P7",[]),
 ("F5.4","'What changed since the last VERIFIED case' diff as a context block for recurring components","PROTOTYPE","Links corpus lineage to context.",2,1,"L","L","P2",[]),
 ("F5.5","Context receipts: every injected block carries a reason code (test-import, diff-touched, retrieved-case) for later attribution without instrumentation","IMPLEMENT_NOW","Makes 5.10 possible cheaply.",2,0,"L","L","P7",[]),
 ("F5.6","Hard cap per slot with overflow → summary-by-tool (deterministic: signatures + docstrings), never summary-by-model","IMPLEMENT_NOW","Deterministic compression cannot hallucinate.",2,1,"L","L","P7",[]),
 ("F5.7","Untrusted-data slot is separate from code/evidence slots and always framed (F-006 marker) — compiler enforces, not the caller","IMPLEMENT_NOW","Security property of the context compiler itself.",3,0,"L","L","P7",[]),
 ("F5.8","Context replay for verification: verifier gets the same manifest hashes to prove it looked at the same code","IMPLEMENT_NOW","Ties context to evidence.",2,0,"L","L","P1",[]),
 ("F5.9","Prompt-cache-aware layout: stable prefix (policies, invariants, tool schemas) first, volatile slices last, so cached-prefix hit rate is maximal","IMPLEMENT_NOW","Direct token/latency saver; core context.py already orders blocks for KV cache — extend to slices.",3,0,"L","L","P7",[]),
 ("F5.10","Repo map cached per HEAD and shared across agents/sessions (file, not prompt)","IMPLEMENT_NOW","Discovery cost paid once per commit, not per session.",3,1,"L","L","P7",[]),
 # M6 — router
 ("F6.1","Route on FAILURE-PATTERN posterior, not on average success: P(model solves | this error class)","PROTOTYPE","Sharper than class-average; needs P3 rows keyed by classify_error.",2,1,"L","M","P3",[]),
 ("F6.2","Local-first escalation ladder with verified checkpoints: local attempt must produce a reproducible failing test before cloud is considered","IMPLEMENT_NOW","Cloud pays for uncertainty, not for discovery; also security-aligned (fail-closed cloud).",3,0,"L","L","P4",[]),
 ("F6.3","Router decisions carry an explanation record in the learning corpus when the task ends (chosen/rejected/why/outcome)","IMPLEMENT_NOW","Router already stores route explanations; add outcome link.",2,0,"L","L","P3",[]),
 ("F6.4","Warm-model bias with explicit cost: switching cost (load time × VRAM) is part of the score, not a tie-breaker","IMPLEMENT_NOW","Formalizes 6.9.",2,0,"L","L","-",[]),
 ("F6.5","Capability probes are verification tasks: a model earns a capability tag only by passing a fresh probe (exists: model_capability_checks) — never by self-description","IMPLEMENT_NOW","Already partially; make advertised caps non-scoring when a probe exists.",2,0,"L","L","P1",[]),
 ("F6.6","Refuse-to-route: when no candidate meets evidence-based requirements, return OWNER_DECISION_REQUIRED rather than best-effort","IMPLEMENT_NOW","Fail-closed routing for high-risk classes.",2,0,"L","L","-",[]),
 ("F6.7","Route audit invariant test: for every task with cloud disabled, model_calls.is_cloud must be false (nightly self-check over the DB)","IMPLEMENT_NOW","Continuous verification of F-008/F-016.",3,0,"L","L","P1",[]),
 ("F6.8","Per-model output-schema compliance counter (tool-call JSON validity) feeding the reliability prior","IMPLEMENT_NOW","Cheap counter; local models fail here most.",2,0,"L","L","P3",[]),
 ("F6.9","Shadow-route: run the cheaper candidate in shadow on a sample of tasks to build competence data without risking outcomes","DEFER","Needs shadow lane wiring.",2,2,"L","M","P5",["P5"]),
 ("F6.10","Route by context fit: candidate window must fit the compiled manifest with headroom for evidence; else route up or compress deterministically","IMPLEMENT_NOW","Prevents silent truncation failures.",2,0,"L","L","P7",[]),
 # M7 — adaptive compute
 ("F7.1","Verification-first budgeting: allocate verify budget before generate budget; generation stops when verify reserve would be breached","IMPLEMENT_NOW","Inverse of the usual order; protects the only thing that matters (VerifiedSuccess).",3,0,"L","L","P4",[]),
 ("F7.2","Escalate on EVIDENCE GAP, not on failure count: retry with the same model only if a new observation was obtained","IMPLEMENT_NOW","Failure count is a proxy; evidence gap is the cause.",3,0,"L","L","P4",[]),
 ("F7.3","Checkpoint-or-stop rule: any unit of work larger than N minutes must produce a committed artifact or a recorded PARTIAL state","IMPLEMENT_NOW","This session's operational lesson; encode in agent templates.",3,0,"L","L","-",[]),
 ("F7.4","Compute ladder with fixed rungs (local-fast → local-strong → cloud) and a hard cap on rungs per task class","IMPLEMENT_NOW","Deterministic escalation; cost bounded.",2,0,"L","L","P4",[]),
 ("F7.5","Cost of being wrong estimates from risk_class drive the verification rung, not the generation rung","IMPLEMENT_NOW","Spend on checking where mistakes are expensive.",2,0,"L","L","P4",[]),
 ("F7.6","Early-stop on 'no new hypothesis' — if a retry produces the same hypothesis set, stop and escalate","IMPLEMENT_NOW","Concrete diversity check (7.2).",2,0,"L","L","P4",[]),
 ("F7.7","Session-limit awareness as a first-class signal (remaining budget from the provider) → shrink protocol depth ahead of the wall","PROTOTYPE","Providers expose rate-limit headers; use them.",2,1,"L","L","P4",[]),
 ("F7.8","Cheap parallel probes are deterministic tools only (never parallel model calls) — parallelism budget spent on tests, not tokens","IMPLEMENT_NOW","Clarifies 7.6 to avoid token blowups.",2,0,"L","L","P4",[]),
 ("F7.9","Escalation requires a handoff packet: failing test, hypotheses, ledger — the stronger model never re-discovers","IMPLEMENT_NOW","Biggest cloud-cost saver: strong models start at the frontier.",3,0,"L","L","P7",[]),
 ("F7.10","Per-task 'verified progress per token' metric logged to the flight recorder","IMPLEMENT_NOW","The optimization target must be measured to be optimized.",2,0,"L","M","P3",[]),
 # M8 — counterexamples
 ("F8.1","Boundary mutator library: path (../, symlink, junction, encoded), identity (name re-register), URL (numeric IP forms, redirect), header (missing/garbage), approval (replay, tamper)","IMPLEMENT_NOW","Every SECREM test in this session used these by hand; make them importable fixtures.",3,1,"L","L","P6",[]),
 ("F8.2","Invariant-derived counterexamples: for a predicate 'resolved(p) ∈ roots' auto-generate the 4 canonical violations","IMPLEMENT_NOW","Predicate → violation catalog is finite and reusable.",3,1,"L","L","P6",[]),
 ("F8.3","Fix-shaped attacks: given a diff, generate variants that target exactly the branch the fix added (e.g., 'what does the fix NOT check')","PROTOTYPE","Uses diff + predicate list; strong for review.",2,1,"L","L","P6",[]),
 ("F8.4","Sibling sweep: when a boundary bug is fixed in one component, run its counterexamples against all components sharing the boundary tag","IMPLEMENT_NOW","F-004/F-010/F-017 were the same SSRF class in three places.",3,0,"L","L","P6",[]),
 ("F8.5","Counterexamples as first-class corpus entries with reproduction commands; retrieval by boundary","IMPLEMENT_NOW","adversarial_variants field exists; add boundary tag index.",2,0,"L","L","P2",[]),
 ("F8.6","Must-stay-denied ratchet: negative tests are pinned (like KNOWN_SHELL_EXCEPTIONS) so weakening a check fails CI","IMPLEMENT_NOW","Pattern exists in stage13 hostexec test; generalize.",2,0,"L","L","P6",[]),
 ("F8.7","Deterministic fuzz budget per boundary (N seeds, fixed RNG) run in CI; failures become NOT_REPRODUCIBLE tasks with the seed","PROTOTYPE","Bounded, reproducible fuzzing.",2,1,"L","L","P6",[]),
 ("F8.8","Untrusted-content corpus: injection strings (SYSTEM:, ignore previous, ANSI, bidi) reused across MCP/browser/memory tests","IMPLEMENT_NOW","Exists piecemeal in tests; centralize.",2,0,"L","L","P6",[]),
 ("F8.9","Sequence attacks over approvals: approve→re-register→resume; approve→tamper→resume; consumed→replay — as a generic harness for any approval-gated action","IMPLEMENT_NOW","F-013/F-015 shape; reusable.",2,1,"L","L","P6",[]),
 ("F8.10","Counterexample credit: a variant that fails counts as a finding with its own learning record (FAILED_EXPERIMENT for the fix)","IMPLEMENT_NOW","Closes the loop between attack and corpus.",2,0,"L","L","P2",[]),
 # M9 — skill factory
 ("F9.1","Skills are verification recipes first, generation recipes second: a skill must ship its ExpectedState template","IMPLEMENT_NOW","Aligns skills with verifier-first.",3,0,"L","L","P1",[]),
 ("F9.2","Skill minting requires ≥3 VERIFIED cases sharing (bug_class, boundary) and one independent re-run","IMPLEMENT_NOW","Concrete promotion rule.",2,0,"L","L","P5",[]),
 ("F9.3","Skills carry NEGATIVE examples (tempting wrong fix) as part of the contract","IMPLEMENT_NOW","From F1.2; skills inherit it.",2,0,"L","L","P2",[]),
 ("F9.4","Skill blast radius declared as allowed_paths/capabilities; runtime enforces (Deep Fix scope gate)","IMPLEMENT_NOW","Skill cannot widen authority by construction.",3,0,"L","L","P1",[]),
 ("F9.5","Skill A/B is Direct vs Skill with the SAME model on hidden holdout; promotion needs VerifiedSuccess +Δ and no security regression","IMPLEMENT_NOW","Owner's Stage 3 made concrete.",3,1,"L","L","P5",[]),
 ("F9.6","Skill retirement when its cases are superseded (F1.7)","IMPLEMENT_NOW","Lifecycle symmetry.",1,0,"L","L","P2",[]),
 ("F9.7","Skill size budget in tokens with a measured cache-hit requirement (stable prefix)","PROTOTYPE","Keeps skills cheap at runtime.",2,1,"L","L","P7",[]),
 ("F9.8","Skill = protocol fragment + probes + verification recipe, never a system-prompt blob","IMPLEMENT_NOW","Composable with F2.1.",2,0,"L","L","P1",[]),
 ("F9.9","Model-specific skill variants only when measured (F9.5) — otherwise one canonical skill","IMPLEMENT_NOW","Avoids N×M skill explosion.",1,0,"L","L","P5",[]),
 ("F9.10","Skill provenance chain (cases → skill version → outcomes) in the corpus","IMPLEMENT_NOW","Auditability; same store.",2,0,"L","L","P2",[]),
 # M10 — self-improvement lab
 ("F10.1","Improvement claims are learning records with status; nothing is 'promoted' outside the corpus lifecycle","IMPLEMENT_NOW","One lifecycle for fixes, skills, protocols, routes.",3,0,"L","L","P5",[]),
 ("F10.2","Holdout is a git-tracked set of (test id, expected verdict) with hashes; candidates run in a workspace where holdout files are absent","IMPLEMENT_NOW","Contamination-proof by construction.",3,1,"L","L","P5",[]),
 ("F10.3","Minimum evidence for promotion is task-count-based with an honest power note (n<20 → 'insufficient evidence', never 'improved')","IMPLEMENT_NOW","Replaces fake statistics with honesty.",2,0,"L","M","P5",[]),
 ("F10.4","Security gate is a veto, not a score term: any must-deny test failing blocks promotion regardless of VerifiedSuccess gain","IMPLEMENT_NOW","Non-negotiable per owner; encode as code.",3,0,"L","L","P5",[]),
 ("F10.5","Experiment registry rows are learning records too (status=UNVERIFIED until independent re-run)","IMPLEMENT_NOW","Reuse validator/store.",2,0,"L","L","P5",[]),
 ("F10.6","Ablation-by-gate: each Deep Fix gate can be disabled in the lab only; production always full — lab measures which gate buys VerifiedSuccess","PROTOTYPE","Answers 'ceremonial or not' with data.",3,1,"L","L","P5",[]),
 ("F10.7","Rollback is a corpus operation: mark the promoted record REJECTED with evidence; retrieval instantly stops preferring it","IMPLEMENT_NOW","Rollback without deploys.",2,0,"L","L","P2",[]),
 ("F10.8","Owner promotion UI shows the evidence ledger diff (before/after) rather than a summary","PROTOTYPE","Human decision on evidence, not prose.",2,1,"L","L","P1",[]),
 ("F10.9","Longitudinal re-verification is a scheduled task with the same verifier; drift = a new FAILED_EXPERIMENT record","DEFER","Needs scheduler wiring; design is trivial.",2,1,"L","L","P5",[]),
 ("F10.10","No self-modifying prompts in production paths: prompt changes are candidates in the lab with the same lifecycle","IMPLEMENT_NOW","Closes the 'better because it says so' loop.",3,0,"L","L","P5",[]),
]

RISK = {"L": 0, "M": 1, "H": 2}


def _record(idea, source: str) -> dict:
    iid, summary, decision, why, benefit, cost, sec, good, prim, deps = idea
    mech = int(iid.replace("F", "").split(".")[0])
    conf = {"IMPLEMENT_NOW": 0.7, "PROTOTYPE": 0.5, "DEFER": 0.4, "REJECT": 0.6}[decision]
    reuse = 2 if prim in ("P1", "P2", "P4", "P5", "P6", "P7") else 1
    coverage = 2 if mech in (4, 8, 10) else 1
    evi = benefit * conf * reuse * coverage
    risk_pen = RISK[sec] * 2 + RISK[good]
    aid = round(evi / max(0.5, cost + 0.5) - risk_pen, 2)
    return {
        "IDEA_ID": iid, "PARENT_MECHANISM": f"M{mech}: {MECHANISMS[mech]}", "AUTHOR_SOURCE": source,
        "SUMMARY": summary,
        "PROBLEM": f"Mechanism {mech} ({MECHANISMS[mech]}) — see summary",
        "HYPOTHESIS": f"If implemented, VerifiedSuccess/cost improves via primitive {prim}",
        "EXPECTED_BENEFIT": f"{benefit}/3 (ESTIMATE)", "EXPECTED_COST": f"{cost}/3 (ESTIMATE)",
        "SECURITY_RISK": sec, "GOODHART_RISK": good, "DEPENDENCIES": deps,
        "OVERLAPS": prim, "BENCHMARK": ("SECREM test suites + hidden holdout VerifiedSuccess"
                                          if mech in (4, 8, 10) else "Direct vs Bossman on holdout; tokens/task"),
        "BASELINE": "session 2026-09-02: 16 VERIFIED cases, 0 measured A/B (ESTIMATE only)",
        "DECISION": decision, "DECISION_EVIDENCE": why,
        "IMPLEMENTATION_STATUS": "NOT_STARTED" if decision != "REJECT" else "N/A",
        "RESULT": "", "VERIFICATION": "", "LESSONS": "", 
        "TEACH_LOCAL_MODEL": f"Decision {decision}: {why}",
        "CONFIDENCE": conf, "LIMITATIONS": "Estimates are ordinal expert judgments, not measurements",
        "PRIORITY_AID": aid,
    }


def build() -> list[dict]:
    return [_record(i, "OWNER") for i in OWNER] + [_record(i, "FABLE") for i in FABLE]


def main() -> None:
    recs = build()
    OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSONL.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in recs),
                         encoding="utf-8")
    counts = {}
    for r in recs:
        counts[r["DECISION"]] = counts.get(r["DECISION"], 0) + 1
    lines = ["# Intelligence ideas — catalogue and decisions", "",
             f"Total {len(recs)} (owner {len(OWNER)}, Fable {len(FABLE)}). Decisions: "
             + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())), "",
             "PriorityAid = EVI/(cost+0.5) − risk penalty, EVI = benefit×confidence×reuse×coverage (ordinal aid, not truth).", "",
             "## Shared primitives", ""]
    lines += [f"- **{k}** — {v}" for k, v in PRIMITIVES.items()]
    for m in range(1, 11):
        lines += ["", f"## M{m}: {MECHANISMS[m]}", "", "| id | src | decision | aid | summary | evidence |", "|---|---|---|---|---|---|"]
        for r in sorted((r for r in recs if r["PARENT_MECHANISM"].startswith(f"M{m}:")),
                        key=lambda r: -r["PRIORITY_AID"]):
            lines.append(f"| {r['IDEA_ID']} | {r['AUTHOR_SOURCE']} | {r['DECISION']} | {r['PRIORITY_AID']} | "
                         f"{r['SUMMARY'].replace('|', '/')} | {r['DECISION_EVIDENCE'].replace('|', '/')} |")
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(len(recs), counts)


if __name__ == "__main__":
    main()
