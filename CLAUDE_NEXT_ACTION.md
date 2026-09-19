# CLAUDE / ASTRA NEXT ACTION — CONTINUE OWNER CONVERGENCE

Canonical branch: `release/bossman-owner`. Existing PR: #67.

Continue the published product, not a new project. Do not create another final branch, change the default branch, merge #67 into its old base for appearances, force-push, reset published history, delete historical branches, or discard another integrator's work.

## 1. Current takeover checkpoint — 2026-09-19

START_SHA: `5a7155df3795d8010b31148b67df76c512385b7b` (Claude).

During this run the remote advanced to `8d8ed627bd4f9ffd534f0b038d027a916fa38dda`. Its only change is `tests/owner_scenarios/OWNER_HARDWARE_FIRST_RUN.md`; preserve it. The MVCR hardware scenario is a future owner acceptance task, not permission to access owner records or submit an application during engineering tests.

The certificate for `b2b9b2e8bb1ec4508a3346a2389e654e18c2cf65` remains historical. It does not certify this patch or the new branch tip. No unpushed Claude workspace was recovered or claimed recovered.

Two unfinished audit leads were reproduced against published production code and corrected. These are targeted fixes, NOT full product acceptance.

### Context: confirmed cross-project cache collision

Before: document identity used source URI and content hash without project. Ingesting identical bytes at the same URI in project B returned a Document for B, but reused A's cache and persisted no searchable B index.

After: resolve cached IDs using exact project/source/hash equality, including the default scope. New IDs encode the scope tuple unambiguously. Existing legacy IDs and chunk references are reused only in their actual project. A non-destructive lookup index is added; no owner data is deleted.

Regression: `bossman-core/tests/test_context_ingest_project_scope.py` (10 tests). Real SQLite, FTS and portable search, identical relative paths, named/default scopes, legacy-reference preservation, changed text isolation, ContextEngine -> real ContextBuilder, and a fresh subprocess reopening the durable database. Deterministic HashEmbedder; no model request.

The initial nine-test version failed 6 tests on the old code and passed after the patch. The tenth adds ContextBuilder and fresh-process evidence. This does not yet prove every UI ingestion path, the async accepted/ready distinction, or a live model's answer.

### Approvals: confirmed lease-grant replay

The existing atomic `approved -> consumed` operations were retained. The defect was their surrounding HTTP lease path: `decide()` could return an already processed/rejected row while the handler still minted new authority. Eight concurrent HTTP deliveries created eight leases. Invalid lease preparation could return 409 after already committing approval.

After: only the pending-decision CAS winner prepares a lease; the decision, parked-call validation and lease insertion share one database transaction. Events are emitted after commit. Failed preparation rolls back both records. Replay cannot mint or expand a lease. Task/run/effect binding and terminal-task rejection are checked. Existing no-lease decisions remain idempotent.

Regression: `command-center/tests/test_approval_lease_replay.py` (15 tests). Authenticated production FastAPI routes with real SQLAlchemy/SQLite, duplicate and eight-way parallel delivery, rejected/revoked/expired/consumed rows, cancelled task, unrelated task/run, wrong token, spent capacity after DB reopen and fresh process, rollback after INSERT, durable lease before worker notification, and plain-decision compatibility.

The initial eleven-test version failed 10 tests on old code. Tests use generated fixture records, not owner data. No external action is dispatched; these tests do not certify Telegram delivery, document-revision replay, all worker recovery paths or external exactly-once execution.

### Actual local evidence and its limits

Combined targeted result: **25 passed**, most recent pre-publication run **15.13 s** on Linux / Python 3.13. This is source/process-integration regression evidence, not OWNER SCENARIOS, not Windows acceptance, and not AI_BACKED_CI.

Source bytes came from the actual published Linux Local bundle for START_SHA, run `35443460718`, artifact `10584626739`. Download SHA256 independently matched `a657fceadddcea9848b107abd5f3f93c7687fb03735560239e4cbbb61d24e946`; touched original modules matched repository Git blob hashes. The package was extracted to a source working snapshot, not installed as a new candidate. GitHub network cloning and PyPI installation were unavailable in the engineering container. Its missing aiosqlite dependency was supplied from upstream 0.21.0 source for local tests only, with shortened comments/docstrings; no database fake, no dependency replacement committed. Re-run with normal declared dependencies and the repository's complete test harness in CI.

A GitHub Git-data fast-forward publication is used because shell Git cannot reach GitHub. Uploaded blobs are checked against the locally tested Git blob hashes. Publication, CI completion, and installed-product certification remain separate facts.

## 2. Owner decisions are already made — do not ask again

The former questions in historical records are superseded by these explicit owner choices. Acceptance of a policy is not proof of its implementation:

- SECURITY-001: `curl ... | sh` is ASK, not AUTO and not blanket DENY. Show source, downloaded-code execution, environment and rights; bind consent to the actual command/content. Changed content or command requires new approval. Preserve sandbox, system-directory restrictions and secret redaction. This is not blanket permission to run commands now.
- CONTROL-001: necessary coordinate fallback is allowed. Prefer semantic targets; check fresh observation, active window, current target and result. Confidence=1 alone is not evidence. Preserve approvals, retry bounds and ambiguity handling.
- OS-64: deleting html/body is allowed only after the explicit warning: **«Будет удалено ВСЁ содержимое этого сайта/документа. Продолжить?»** Preserve a verified recovery version first. Bind approval to document, operation and current revision. Prove denial without mutation, permitted deletion once, stale/replayed rejection and full restoration.

These three complete policies are NOT certified by the two fixes above. Update their entries in the existing `CONVERGENCE_DECISIONS.md` and verify real product paths; do not mark owner scenarios green merely because permission was given.

## 3. Keep the ONE existing owner registry

Continue `tests/owner_scenarios/owner_scenarios.json` (110 scenarios at takeover). Do not create a second registry or restart the first twenty. The previous version of this handoff's first-twenty milestone is obsolete.

Maintain separate REGRESSION CI and OWNER SCENARIOS counts. Historical 102/110 and 48 installed_product are not measurements of the new candidate.

For each existing scenario distinguish outcome (PASS, FAIL, NOT_RUN, BLOCKED_EXTERNAL, INSUFFICIENT_EVIDENCE), environment, model kind, and actual evidence. Source TestClient/create_app is not installed-product evidence. Deterministic tests without model requests are not AI_BACKED_CI. A model-backed run needs a real available authorized backend through Bossman's provider contract, never the engineering assistant's identity or scripted answers. Do not read old keys from chat or logs; record exact required secret names after inspecting the actual CI adapter.

## 4. Product work still requiring execution

Finish the complete context/approval chains, including revision-bound destructive edits, task recovery, denied effects and authenticated Telegram callbacks. For external systems without idempotency guarantees, reconcile actual state or stop for review after an uncertain send; never blindly repeat or promise universal exactly-once.

Image Studio: the existing journal reports MockImageProvider and an external FFmpeg transformation, not a proven editor transform. Check actual product code. Missing adapter/operation is engineering work, not an owner-PC blocker. Separate import/edit/save/reopen proof from generation/provider proof.

Video Studio: use the product's actual operation/render/export path; validate output by ffprobe, full decode and expected edit; reopen project after restart. Do not rewrite a working editor for a new architecture.

Telegram: task -> consent -> authenticated callback -> one continuation -> verified result. Fixture transport proves a contract, not live delivery. Live checks only in an authorized test chat.

Preserve all major capabilities: context/memory, models/agents, computer/browser control, files/terminal, Telegram, photo/video, learning, budget/security, and Windows installation.

## 5. Salvage continuity

Use the existing salvage registry and `CONVERGENCE_DECISIONS.md`; continue unresolved PR59/60/61/62 and newer source changes, not a fresh scan of every branch.

For each unique capability keep candidate provenance, selection evidence, lost-capability check and rollback SHA. Categories remain PRESENT_BETTER_IN_OWNER, PORT_REQUIRED, OBSOLETE, OWNER_HARDWARE_ONLY. Port confirmed P0/P1 gaps; ancestry or a same-named file does not prove retained behavior. Do not close PRs with useful unported functionality, delete branches, or erase historical records.

## 6. Next verification and publication loop

Reproduce -> failing regression -> production fix -> positive/negative checks -> commit -> safe publication -> relevant CI. Start with targeted tests, then full acceptance of a stable candidate. Recheck remote before each publication and retain any new commits.

Declare a stable candidate through the existing `tools/release_candidate.json`, then run the existing exact-SHA certification machinery. Require every mandatory workflow and job across push, pull_request and dispatch; inspect actual checkout/build/test SHA, artifact hash and scenario registry revision. Queued, skipped, cancelled or missing runs are not PASS. Do not weaken tests or clock measurements to obtain a certificate.

Clean Windows acceptance requires the real candidate package installed outside the source checkout, verified import/resource/UI provenance, startup, browser and recovery evidence. A downloaded OLD bundle cannot prove the new fixes. Local owner hardware/model tests remain separate.

Final reporting must distinguish START_SHA, published SHA, actually TESTED_SHA, report commit and remote tip; CI run/job IDs and exact outcomes; Windows artifact/hash; owner-scenario outcomes with model/environment; remaining P0/P1 and salvage work; missing credentials vs hardware vs code. Never substitute a handoff for repairs or certify the whole product from a targeted regression count.
