import json, sys, pathlib
sys.path.insert(0, r"C:\AiMaxBossman-claude-bossman-control-v03-43igbk\bossman-core")
from bossman.apprentice.fable_direct import DirectApiBudget, FableDirectClient

budget = DirectApiBudget(r"C:\AiMaxBossman-claude-bossman-control-v03-43igbk\docs\mission\REDTEAM-TANDEM-001\budget.json",
                         total_usd=5.00, mission_id="REDTEAM-TANDEM-001", owner_id="bossman")
client = FableDirectClient(model="claude-sonnet-4-5", max_output_tokens=4000, budget=budget)

def ex(rel, start, n=90):
    base = pathlib.Path(r"C:\AiMaxBossman-claude-bossman-control-v03-43igbk\bossman-core\bossman")
    t = (base / rel).read_text(encoding="utf-8")
    i = t.find(start)
    return t[i:i + n * 55] if i >= 0 else "NOT_FOUND:" + start

bundle = {
 "ROLE": "FABLE 5 = PRIMARY red-team attacker, auditor, architect, coder. GLM only applies patches, tests, bypasses, commits. Output structured JSON patches ONLY (no chain-of-thought). mock is never evidence.",
 "MISSION": "REDTEAM-TANDEM-001. State: remote d1ce420 (Opus: F1-F6 fixed+tested, 16 REAL_SANDBOX cases, IQ v2). Nightly: 18/18 covered, READY, 0 failed. Your job: (1) attack benchmark for false READY - design RED tests; (2) design FableEscalationPolicy module; (3) design dashboard Fable API wiring. GLM will apply your patches verbatim if tests pass.",
 "CURRENT_ENGINE_GATE": ex("benchmark\\engine.py", "def _gate", 80),
 "CURRENT_SANDBOX_ROW": ex("benchmark\\sandbox_row.py", "class ", 60),
 "FABLE_DIRECT_IFACE": ex("apprentice\\fable_direct.py", "class DirectApiBudget", 55),
 "GATEWAY_APP": ex("gateway\\app.py", "app =", 70),
 "KNOWN_INVARIANTS": "provenance ShaMismatch guard; manifest MAC pinning opt-in (OFF default); evidence_class runner-assigned; 18 REQUIRED_CAPABILITIES; strict tiers refuse NO-GO; CLI exit 1 on not-ready; DirectApiBudget atomic cross-process; budget events durable JSON",
 "TASK1_BENCHMARK_ATTACK": "Design up to 6 most valuable RED tests for false-READY attacks NOT yet covered: statistics gaming (NaN/Inf/div0/missing=1), report tampering after run, weight mutation post-result, n=1 significance, evidence self-report via sandbox_row, holdout leakage. For each: exact test code + exact minimal fix. Reference real symbol names from provided excerpts only.",
 "TASK2_ESCALATION_POLICY": "Design bossman/apprentice/escalation.py: FableCallDecision, FableEscalationPolicy (EV_Fable vs EV_Local per spec), FableBudgetForecaster, FableOutcomeTracker (SQLite-free: JSON durable), FableReliabilityProfile. Use DirectApiBudget for reservation. Hard escalation triggers + forbidding rules per mission. Full module code.",
 "TASK3_DASHBOARD_API": "Design additions to gateway/app.py: POST /api/fable/preview (pre-call contract: reason, model, worst-case cost, remaining budget, expected tokens, cache eligibility, cheaper alternative), POST /api/fable/run (reserve->call->verify->settle), GET /api/fable/runs/{id}, GET /api/fable/budget, GET /api/fable/economics, POST /api/fable/cancel. Never expose API key/prompt body. Reuse existing app patterns from excerpt. Full code for new routes.",
 "OUTPUT_SCHEMA": "JSON only: attacks:[{id,severity,red_test_code,fix_code,invariant}], escalation_module_code, dashboard_routes_code, open_questions"
}

result = client.run(bundle)
usage = client.usage[-1] if client.usage else {}
out = {"usage": usage, "verdict_raw": str(result.get("log_text", ""))}
pathlib.Path(r"C:\AiMaxBossman-claude-bossman-control-v03-43igbk\docs\mission\REDTEAM-TANDEM-001\fable_call1.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
print("SAVED")
