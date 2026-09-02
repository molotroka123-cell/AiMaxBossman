# KEY_FUNCTION_TOUCH_MATRIX — LONGHORIZON-FREEZE-001 (runtime-populated)

Every row below was exercised by RUNTIME during this mission (command + result),
not by static reading. NO_CHANGE rows carry the runtime evidence that justified it.

| ID | Symbol/path | Runtime exercise | Result | Weakness found | Change | Tests / verifier |
|---|---|---|---|---|---|---|
| KEYFUNC-FABLE-003 | prompt-cache/cost governor (`bossman/apprentice/fable_direct.py` DirectApiBudget) | live direct-API teacher call with atomic worst-case reservation | PASS | none blocking (single reservation bookkeeping placeholder kept) | NO_CHANGE_JUSTIFIED (single-call mission scope; multi-reservation = POST_FREEZE_BACKLOG) | test_teacher_live PASS |
| KEYFUNC-FABLE-005 | UCA observe→act→verify→recover (`apprentice/engine.py` + `computer_operator/adapters/playwright_browser.py`) | real Chromium loop, 4-step task, side-effect receipt, approval gate | PASS (SUCCEED, semantic-only targets) | accessible_name API misuse; observer id counter reset → freshness violations — both fixed during runtime | YES (playwright_browser.py) | test_e2e_real_gui PASS |
| KEYFUNC-FABLE-006 | skill record/select/reuse (`teacher.learned_strategy`, `skills.attach_verification`) | Bug A→B live: selector refused UNVERIFIED, accepted VERIFIED; reuse zeroed teacher calls | PASS | promotion needs fresh bound evidence — production rule enforced correctly | YES (test strengthened to production path per audit) | test_teacher_live PASS |
| KEYFUNC-FABLE-007 | teacher bundle/patch/verifier (`teacher.py`, `fable_direct.py`) | REAL Claude direct-API repair cycle; sanctions active | PASS | `observe_teacher` crashed on list-shaped untrusted `test_results` — hardened to coerce | YES (teacher.py) | test_apprentice_teacher 19 PASS |
| KEYFUNC-FABLE-008 | durable side effect + owner approval (`durable.py`, `guards.py`) | real spawn/kill restart; nonce/cooldown/pending/reliability survival; issued-approval enforcement | PASS | none | NO_CHANGE_JUSTIFIED (refusals observed: replay, model-minted approval, same-run verifier) | pytest -m restart 2 PASS; test_durable_live_owner_auth PASS |
| KEYFUNC-FABLE-009 | real browser E2E (`playwright_browser.py`) | headless Chromium + local page + Higgsfield real attempt | PASS / BLOCKED_BY_ENVIRONMENT (Higgsfield auth wall, evidence captured) | EffectReceipt protocol mismatch found & fixed in adapter | YES (adapter) | test_e2e_real_gui PASS |
| KEYFUNC-FABLE-011 | benchmark/release evidence (`bossman/benchmark`) | release tier executed at a21512f (READY: regression 1.0 n=21, real 1.0 n=4, live honest 0) | PASS | none | NO_CHANGE_JUSTIFIED | bench report in mission docs |
| KEYFUNC-FABLE-012 | CI/release gate (`bossman-core-ci.yml`, `bossman-v2-repair.yml`) | CI-HISTORY-001 fix + auto-repair honesty + fetch-depth 0 | PASS (per fresh GitHub audit + contract tests) | shallow-history class bug in 3 workflows | YES (workflows) | tests/test_autorepair_workflow_contracts.py 2 PASS |
| KEYFUNC-FABLE-001/002/004/010 | mission queue/routing/WorldState/ZIP3 | mission capsule continuity (real restart), routing by evidence hierarchy, ZIP3 ingest verification | PASS | stale checkpoint (FABLE_FINAL_GAPS_STATE) — refreshed | YES (checkpoint docs) | RESTART_RESUME_PROOF.md, ZIP3_INGEST_REPORT.md |

Verdict: 6 components improved with targeted tests; 3 verified NO_CHANGE_JUSTIFIED with runtime evidence.
