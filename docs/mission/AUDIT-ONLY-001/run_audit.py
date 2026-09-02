import json, sys, pathlib
sys.path.insert(0, r"C:\AiMaxBossman-claude-bossman-control-v03-43igbk\bossman-core")
from bossman.apprentice.fable_direct import DirectApiBudget, FableDirectClient

budget = DirectApiBudget(r"C:\AiMaxBossman-claude-bossman-control-v03-43igbk\docs\mission\AUDIT-ONLY-001\budget.json",
                         total_usd=3.00, mission_id="AUDIT-ONLY-001", owner_id="bossman")
client = FableDirectClient(model="claude-sonnet-4-5", max_output_tokens=1600, budget=budget)

def ex(rel, start, n=110):
    base = pathlib.Path(r"C:\AiMaxBossman-claude-bossman-control-v03-43igbk")
    clean = rel.strip("\\")
    src = (base / "bossman-core" / "bossman" / clean) if not rel.startswith("..") else (base / "bossman_shared" / rel.split("\\")[-1])
    t = src.read_text(encoding="utf-8")
    i = t.find(start)
    return t[i:i + n * 60]

bundle = {
 "PROBLEM_ID": "AUDIT-ONLY-001/CONSOLIDATED",
 "ROLE": "FABLE_5_FULL_SYSTEM_SECURITY_AUDITOR. Audit-only. Return verifiable findings with exact code paths; mock is never evidence. No patches, only findings + patch plans.",
 "TRUST_BOUNDARY_MAP": "owner(trusted)->GLM orchestrator(semi)->Fable teacher(UNTRUSTED, hermetic)->verifier(independent principal)->planner->executor/actuator(EffectReceipt protocol)->browser/filesystem/shell(UNTRUSTED content)->memory/learning store(redacted, versioned)->benchmark(provenance-bound)->budget ledger(durable, cross-process lock)->approvals(owner-issued nonce only)",
 "ALREADY_VERIFIED_BY_GLM_EVIDENCE": "owner-issued approvals (model-minted refused by test), replay/expiry/digest-tamper denied, durable store survives real process kill, teacher hermetic env scrubbed + hidden-reasoning dropped, evidence_class runner-assigned (lying child FAILs), benchmark gate: 18 capabilities + strict tiers + MAC pinning + exit 1 on NO-GO, DirectApiBudget: cross-process 5x reserve under $3 cap, reconciliation holds on failure, secret scan PASS, CI green (4 workflows)",
 "UNREVIEWED_AREAS_FOR_YOU": {
  "durable_store_internals": ex("\\apprentice\\durable.py", "class DurableSafetyStore", 70),
  "deep_fix_rollback": ex("\\deep_fix.py", "def propose", 60),
  "learning_guard_promotion": ex("\\learning_guard\\promotion.py", "def ", 70),
  "cache_intelligence": ex("..\\..\\bossman_shared\\cache_intelligence.py", "class ", 50)
 },
 "MISSING_SCOPE_CONFIRMED": "no trading-learning module exists anywhere in repo (rg/glob) - section 'trading learning' = NOT_IMPLEMENTED",
 "QUESTIONS": ["Q1: In durable.py internals - any lost-update/corruption/fail-open path GLM's tests could miss (no cross-HOST story)?", "Q2: deep_fix rollback - protected-file bypass or stale-evidence acceptance?", "Q3: learning_guard promotion - promotion without baseline / cross-corpus poisoning?", "Q4: cache_intelligence - false savings claims / poisoning of local reuse?", "Q5: benchmark math (SystemIQ weights 30/15/10x4/5, linear sum, PureCodingIQ 40/20/15/15/10) - is linear-sum gameable vs geometric mean + hard gates? Propose replacement ONLY with proof of superiority.", "Q6: rank the 16 unmeasured benchmark capabilities by security-critical priority (top 6)."],
 "REQUESTED_OUTPUT": "JSON only: {\"findings\":[{\"id\",\"severity\":\"P0|P1|P2\",\"area\",\"evidence\":\"exact code path\",\"exploit\",\"fix_plan\"}],\"benchmark_math_verdict\",\"capability_priority\":[6 ids],\"scores\":{\"security\":n/10,\"uca\":n/10,\"memory\":n/10,\"benchmark\":n/10,\"overall\":n/10}}"
}

result = client.run(bundle)
usage = client.usage[-1] if client.usage else {}
out = {"usage": usage, "verdict_raw": result.get("log_text", "")[:8000]}
pathlib.Path(r"C:\AiMaxBossman-claude-bossman-control-v03-43igbk\docs\mission\AUDIT-ONLY-001\fable_consolidated.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
print("USAGE:", json.dumps(usage))
print("RAW-TAIL:", result.get("log_text", "")[-2200:])
