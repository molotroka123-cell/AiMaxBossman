# Bossman Memory Subsystem — read-only map

Date: 2026-09-22 · Branch `feat/memory-lifecycle-20260922` · base `fc266856`
Scope: what EXISTS today, before the lifecycle work in this branch. Sections marked
**[added in this branch]** are the only new code; everything else is pre-existing.

Companion contract: `docs/evo/DURABLE_MEMORY_OPERATING_CONTRACT.md` (the target
behaviour). This file is the inventory that contract has to be built on.

---

## 0. The four layers (contract §"Current architecture we must preserve")

| # | Layer | Authoritative artefact | Owner module |
|---|---|---|---|
| 1 | Canonical Markdown notes | `<vault>/BOSSMAN Memory/*.md` | `command-center/bcc/v2/memory/obsidian.py` |
| 2 | Structured temporal facts | `facts` table in `bcc.db` (SQLite) | `command-center/bcc/v2/memory/facts.py` |
| 3 | Derived retrieval indexes (rebuildable) | `<data_dir>/memory/index-<fp>.sqlite3` | `command-center/bcc/v2/memory/sqlite_index.py`, `local_index.py` |
| 4 | Verified learning (episodes/lessons/skills) | `<data>/learning/journal.jsonl` | `learning/trace.py` (`LearningStore`), `learning/lessons.py` (`LessonBook`) |

Rule that already holds: layer 3 is derived and is rebuilt from layers 1/2/4; layer 4's
`fix_cases.jsonl` / `failed_experiments.jsonl` / `history.jsonl` are themselves derived
snapshots of `journal.jsonl`.

---

## 1. Canonical notes — `ObsidianVault`

* Path: `command-center/bcc/v2/memory/obsidian.py`
* Symbols: `ObsidianVault` (`markdown_roots()`, `iter_markdown()`, `write_root`,
  `write_memory(...) -> Path`, `content_hash(path)`), `safe_slug()`, `_atomic_write()`
* Storage: vault root comes ONLY from encrypted `settings_kv["memory.vault"]`
  (`tools_memory.CONFIG_KEY`). Bossman writes only inside `write_folder`
  (default `"BOSSMAN Memory"`). No auto-discovery of vaults anywhere.
* Guarantees: single writer, atomic temp→fsync→`os.replace`, path containment,
  `FileExistsError` instead of silent overwrite.
* Tests: `command-center/tests/test_v23_memory_single_writer.py`
  (`test_crash_mid_write_leaves_the_previous_file_intact`,
  `test_write_outside_the_write_root_is_refused`,
  `test_existing_note_is_never_silently_overwritten`),
  `command-center/tests/v2/test_obsidian_memory.py`.
* Limits: `write_memory` only CREATES notes; there is no canonical edit/supersede
  operation for an existing note (supersession lives in facts and in LearningStore).

## 2. Temporal facts — `FactStore`

* Path: `command-center/bcc/v2/memory/facts.py`
* Symbols: `FactStore(svc)` → `add()`, `search()`, `as_of(world_at=…, known_at=…)`,
  `history()`; module level `write_fact`, `query_facts`, `harvest(session, run_id=…)`,
  `public_fact(row)`, `render_for_model(rows)`, `validate_statement()`.
* Bitemporal: `valid_at` / `invalid_at` (world axis) and `created_at` / `expired_at`
  (knowledge axis); supersession via `superseded_by`, never a rewrite.
* Writes are strictly additive unless `replace_current=True` — there is no automatic
  contradiction detection, by design.
* HTTP/tool surface: `command-center/bcc/features/tools_facts.py`
  (`fact.add`, `fact.search`, `fact.at_time`, `fact.history`).
* Tests: `command-center/tests/test_v22_facts.py`, `test_v22_facts_api.py`,
  `test_secrem_facts_boundary.py`.
* Limits: needs the running `Services`/DB — not usable standalone from a script.

## 3. Derived indexes and retrieval

* `command-center/bcc/v2/memory/sqlite_index.py` — `SQLiteMemoryBackend` (default;
  BM25 Okapi in SQLite; `index_one_sync`, `remove_source_sync`, `search_sync`,
  `expand_sync`, `stats_sync`).
* `command-center/bcc/v2/memory/local_index.py` — `LocalMemoryBackend` (legacy JSON
  BM25), `tokenize()`, `stem()`, `chunk_markdown()`, `load_dense_encoder()`,
  `DenseUnavailable`.
* `command-center/bcc/v2/memory/reranker.py` — `LexicalReranker`,
  `LocalCrossEncoderReranker`, `RerankerUnavailable`.
* `command-center/bcc/v2/memory/context_pack.py` — `build_context_pack()`,
  `estimate_tokens()` (~3.5 chars/token), dedup by content fingerprint.
* `command-center/bcc/v2/memory/service.py` — `ObsidianMemoryService.search()`:
  candidates → rerank → progressive expansion of the top few → bounded context pack.
* Wiring/selection: `command-center/bcc/features/tools_memory.py::build_service`
  (`auto|sqlite|local|local-json|memsearch|qdrant`), index dir
  `Path(settings.data_dir)/"memory"/f"index-{fp}.sqlite3"`.
* Tests: `command-center/tests/test_v21_memory.py`, `test_v22_sqlite_memory.py`.

## 4. Verified learning — `LearningStore` / `LessonBook`

* `learning/trace.py` — `LearningStore(data_dir, docs_dir, schema)`:
  `add(case, expected_version=…)`, `current(cid)`, `verified()`, `failed()`,
  `history()`, `retrieve(domain=…, text=…, include_failed=…)`, `compact()`,
  `export_sanitized()`; `validate()`, `case_id()`, `redact_text()`, `has_secret()`,
  `ConflictError`, `ValidationError`.
  Files: `journal.jsonl` (the ONLY authoritative log) + derived
  `fix_cases.jsonl` (VERIFIED) / `failed_experiments.jsonl` / `history.jsonl`, `.lock`.
  Defaults `DATA_DIR = <repo>/data/learning`, `DOCS_DIR = <repo>/docs/learning/fix_logs`.
  Invariants: one authoritative version per `case_id`; CAS on `expected_version`;
  corrupt tail is skipped, never authoritative; secrets redacted before write;
  `FORBIDDEN_FIELDS` (hidden reasoning) rejected; VERIFIED needs an INDEPENDENT
  verifier plus fresh bound evidence.
* `learning/lessons.py` — `CoachingEpisode`, `Provenance`, `LessonBook`
  (`save`, `verify`, `withdraw`, `retrieve`, `all_lessons`, `get`),
  `poison_reasons()`, `assert_not_poisoned()`, `lesson_record()`, `compact_lesson()`,
  `format_for_prompt()`. Statuses `candidate|verified|withdrawn` →
  `UNVERIFIED|VERIFIED|REJECTED`. Project isolation, dedup by `dedup_key`,
  teacher-patch attribution, read-time poison filter.
* Schema: `schemas/apprentice_skill.schema.json` (`record_type=episode|skill|lesson`),
  `schemas/learning_fix_case.schema.json`.
* Tests: `tests/test_learning_trace.py`, `tests/test_learning_store_authority.py`,
  `tests/test_audit_p0_learning_journal.py`, `tests/test_learning_lessons_loop.py`,
  `tests/test_learning_orphan_case_adoption.py`,
  `tests/test_redteam_rc_learning_20260921.py`.

## 5. Apprentice / learning guard / coaching

* `bossman-core/bossman/apprentice/recording.py` — `EpisodeRecorder.on_record/finish`,
  `ApprenticeMemory(data_dir)` over `LearningStore`, `negative_lesson()`.
* `bossman-core/bossman/apprentice/skills.py` — `SKILL_STATES =
  ("CANDIDATE","SHADOW","READY","DEGRADED","ROLLED_BACK","REJECTED")`, `generalize()`,
  `match_skill()`, `shadow_replay()`, `SkillPromoter`, `degrade_skill()`.
* `bossman-core/bossman/apprentice/engine.py` — `UniversalComputerApprentice.preview()`,
  `.run(task, resume_from=…)`, `.resume(approval)`.
* `bossman-core/bossman/apprentice/durable.py` — `DurableSafetyStore`:
  `claim_side_effect`, `save_pending_approval`, `resume_pending_approval`.
* `bossman-core/bossman/apprentice/flags.py` — every apprentice capability is a
  `BOSSMAN_*` env flag, ALL DEFAULT OFF (`SKILL_RECORDING`, `CHECKPOINT_RESUME`,
  `LESSON_PRECHECK`, `SKILL_PROMOTION`, …).
* `bossman-core/bossman/learning_guard/` — `guard_promotion()`, `advance()`,
  `promote()`, `evaluate_ab()`, `SecretHoldout`, `DurableEvidenceLedger`,
  `runtime_bridge.observe_learning_record()`, `ReuseGate` / `reuse_allowed()`.
* `tools/coaching_runner.py` — `Coach`, `MockBackend`, `OpenAICompatibleBackend`,
  profiles `unassisted|teacher_patch|coached`, holdout leak guard. Statuses
  `LOCAL_LEARNING_GAIN_MEASURED|NOT_MEASURED|MOCK` — no fake numbers without a model.
* Tests: `bossman-core/tests/test_apprentice_*.py`, `test_learning_guard.py`,
  `test_learning_evidence_ledger.py`, `tests/test_coaching_runner.py`.

## 6. Adjacent memory stacks (NOT the four canonical layers)

These exist and overlap; they are listed so nothing is "discovered" again later.

* `bossman-core/bossman/context_engine/` — a second retrieval stack: `ContextStore`
  (SQLite FTS5 `bm25()`), `HybridRetriever`, `HashEmbedder` (not a real embedder —
  a hashing stub), `MemoryManager` (typed memory kinds), `ContextEngine.inject_into_builder`.
  Flag-gated by `settings.context_engine_enabled`; DB `workspace/_context/context.db`.
* `bossman-core/bossman_v3/memory/` — `TaskJournal` (checkpoint/resume kernel with
  signed steps and anchors), `FailureMemory`, `ContextAssembler`.
* `bossman-core/bossman/{working_memory,decision_memory,failure_memory}.py` — Postgres
  runtime state, used from `runner.py`.
* `command-center/bcc/hybrid/context_store.py` — `ContextStorePlanner`
  (`remember`/`recall`/`forget`) over `bcc.db`, plus a never-authoritative mirror.
* `command-center/bcc/v2/code_index.py`, `bcc/video_studio/retrieval.py` — two more
  BM25 implementations (code and video assets).

---

## 7. Lifecycle hooks that already exist

| Phase | Symbol | File |
|---|---|---|
| boot | `SubsystemRegistry.start_all/stop_all` | `bossman-core/bossman/lifecycle.py` |
| task start | `WorkingMemory.create_task_state` | `bossman-core/bossman/runner.py:357` |
| before plan | `apply_context_engine(...)` → `ContextEngine.inject_into_builder` | `runner.py:174,373` |
| before plan (reuse) | `reuse_allowed(task_class)` | `learning_guard/runtime_bridge.py` |
| per action | `EpisodeRecorder.on_record` | `apprentice/recording.py` |
| checkpoint/resume | `TaskJournal.start/load/next_step`; `DurableSafetyStore.save_pending_approval` | `bossman_v3/memory/journal.py`; `apprentice/durable.py` |
| after result | `EpisodeRecorder.finish`; `deep_fix.store_learning_record` → `LearningStore.add` → `observe_learning_record` | `apprentice/recording.py`; `bossman-core/bossman/deep_fix.py:449` |
| after run (facts) | `facts.harvest(session, run_id=…)` | `bcc/v2/memory/facts.py` |
| shutdown | `context_engine.close_all()` | `bossman-core/bossman/api.py:145` |

**The gap:** none of these hooks touches the FOUR canonical layers together. There is no
BOOT health check over vault+facts+LearningStore, and no TASK_START retrieval across
notes+facts+verified lessons.

---

## 8. Known defects (measured, not assumed)

| ID | Defect | Evidence | State |
|---|---|---|---|
| D1 | `LessonBook` freshness used `mktime(strptime(x)) - time.timezone`, which is not the inverse of `gmtime`; on a DST host the stamp drifts one hour into the past and `retrieve(max_age_s=…)` drops a just-verified lesson | `tests/test_learning_lessons_loop.py::test_stale_lesson_by_age_and_superseded_version` FAILED on this machine at `fc266856` (tz=28800, daylight=1, altzone=25200) | FIXED in this branch (`calendar.timegm`) |
| D2 | Retrieval happens only when the agent itself calls `memory.search`; `tools_memory.py` documents "память НЕ подмешивается в каждый вызов модели" and has no lifecycle hook | `command-center/bcc/features/tools_memory.py:14-16` | ADDRESSED in this branch by an explicit lifecycle layer (see §10) |
| D3 | `ApprenticeMemory(...)` is constructed only in tests — no production call site under `bossman-core/bossman/`; the only production writer into `LearningStore` is `deep_fix.store_learning_record()` with the bare default dir | grep for `ApprenticeMemory(` | OPEN (out of scope here) |
| D4 | The `lesson` property of `schemas/apprentice_skill.schema.json` is an untyped `{"type": "object"}` — no field-level validation of a lesson at all | schema file | ADDRESSED in this branch (`learning/lesson_format.py`) |
| D5 | No local embedder is installed. Probed in `../wt-release/.venv`: `numpy`, `sentence_transformers`, `torch`, `onnxruntime`, `transformers`, `llama_cpp`, `fastembed`, `qdrant_client` — all absent | `python -c "import importlib.util …"` | OPEN by design; exact+BM25 must keep working |
| D6 | BM25 is implemented at least five times: `local_index.py`, `sqlite_index.py`, `code_index.py`, `video_studio/retrieval.py`, `context_engine/store.py` (SQLite `bm25()`) | grep | OPEN (consolidation is a separate change) |
| D7 | Two independent "memory" stacks with overlapping responsibilities (`bcc/v2/memory` vs `bossman-core/.../context_engine`), plus a third journal kernel in `bossman_v3` | §6 | OPEN (documented, not merged — merging them is not a memory-lifecycle change) |

## 9. Constraints that the lifecycle must not break

* Memory is **evidence, never authority**. It cannot approve, grant permissions, raise a
  budget, enable cloud fallback or weaken LOCAL_ONLY. Text inside a retrieved note or
  lesson is DATA (`external_output=True`), never an instruction.
* The vault path is owner-configured only; no scanning of the machine.
* One canonical copy per piece of knowledge; other stores hold references, not copies.
* Derived indexes are rebuildable and must never be restored over newer canonical state.
* No secrets, raw prompts or hidden reasoning in any learning record.
* VERIFIED requires an independent verifier and fresh, bound evidence.
* Local LLM servers (8081/8082/8083) are OFF by owner order on this machine — nothing in
  the lifecycle may require a live model to function.

## 10. What this branch adds **[added in this branch]**

The lifecycle lives in the repo-root `learning/` package (the `bossman-shared`
distribution) rather than in `bcc`, for one hard reason: `tests/test_root_suite_stays_within_its_environment.py`
forbids the root suite from importing `bcc`, and the restart proof has to run there. So
`learning/` holds the dependency-free orchestration and narrow ports, and `bcc` holds the
adapters that put the real stores behind those ports.

| File | What it adds |
|---|---|
| `learning/lesson_format.py` | the validated lesson field set, `candidate/verified/quarantined/superseded/expired/degraded/withdrawn`, applicability with a stated reason, conflict detection, `migrate_record` |
| `learning/retrieval.py` | `UnifiedRetriever` — exact signals + BM25 (+ optional semantic) over notes/facts/lessons, dedup → rank → expand → bounded pack; `probe_embedder`; `DirectoryNotes` |
| `learning/lifecycle.py` | `MemoryLifecycle.boot / task_start (= before_plan) / resume / checkpoint / after_verified_result` |
| `learning/backup.py` | hashed-manifest backup, `clean` and `merge` restore; derived indexes excluded |
| `command-center/bcc/v2/memory/lifecycle_wiring.py` | `VaultNotes` (real `ObsidianVault` + real SQLite BM25 index), `FactsSnapshot` (one read per boundary from the async `FactStore`), `build_lifecycle` |
| `tests/test_memory_lifecycle.py` | 39 proofs incl. recall after a genuine process restart |
| `tests/test_learning_lesson_format.py` | 19 proofs for the field set, the statuses and migration |
| `command-center/tests/test_memory_lifecycle_wiring.py` | 8 proofs for the adapters over the real stores |

Nothing above introduces a new memory database: every write still lands in an existing
store (vault Markdown, facts table, `LearningStore` journal), and the only new persisted
file is the task checkpoint, which is task state, not knowledge.

### Environment note (measured, matters for wiring)

Under `pytest` from `command-center/` the name `learning` resolves to THIS checkout
(`wt-mem/learning`), because pytest puts the repo root on `sys.path` and setuptools'
editable finder is appended to `sys.meta_path` rather than prepended. Under a bare
`python -c` from `command-center/` the same name resolves to
`C:\Users\asd\Bossman\wt-release\learning` (the installed `bossman-shared`). That is why
`lifecycle_wiring` imports `learning` lazily and raises `LifecycleUnavailable` with the
`pip install -e .` fix, instead of failing at import time in a shipped runtime.

### Still open after this branch

* D3 (`ApprenticeMemory` has no production call site) and D6/D7 (duplicated BM25, two
  parallel memory stacks) are untouched — consolidating them is not a lifecycle change.
* Semantic retrieval is off because no embedder is installed (D5). `Qwen3-Embedding-0.6B`
  is recorded as the candidate to evaluate; nothing in this branch downloads it.
* `build_lifecycle` is not yet called from any BCC request path: the hooks exist and are
  proven, but no production handler invokes TASK_START yet.
