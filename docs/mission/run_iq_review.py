import json, sys
sys.path.insert(0, r"C:\AiMaxBossman-claude-bossman-control-v03-43igbk\bossman-core")
from bossman.apprentice.fable_direct import DirectApiBudget, FableDirectClient

budget = DirectApiBudget(r"C:\AiMaxBossman-claude-bossman-control-v03-43igbk\docs\mission\benchmark_iq_budget.json",
                         total_usd=3.00, mission_id="BENCHMARK-SYSTEM-IQ-001", owner_id="bossman")
client = FableDirectClient(model="claude-sonnet-4-5", max_output_tokens=1200, budget=budget)

engine = open(r"C:\AiMaxBossman-claude-bossman-control-v03-43igbk\bossman-core\bossman\benchmark\engine.py", encoding="utf-8").read()
budget_src = open(r"C:\AiMaxBossman-claude-bossman-control-v03-43igbk\bossman-core\bossman\apprentice\fable_direct.py", encoding="utf-8").read()

def excerpt(src, start, end):
    i = src.find(start)
    j = src.find(end, i + 1)
    return src[i:j if j > i else i + 3500]

bundle = {
 "PROBLEM_ID": "BENCHMARK-IQ-REVIEW-001",
 "ROLE": "independent reviewer of BENCHMARK_SYSTEM_IQ_IMPLEMENTER output (GLM-5 authored; you must verify, not trust)",
 "CURRENT_HEAD": "1377c9e",
 "CHANGES": [
  "benchmark engine: REQUIRED_CAPABILITIES (18 ids), strict tiers nightly/release refuse READY unless every capability has measured REAL_SANDBOX/LIVE coverage, nightly/release manifests non-empty, release_requires_live with LIVE n=0 refused, mock-only evidence refused; CLI exits 1 on NO-GO",
  "SystemIQ (weights 30/15/10/10/10/10/10/5) and PureCodingIQ computed ONLY from REAL_SANDBOX+LIVE rows; REGRESSION rows excluded; INSUFFICIENT_EVIDENCE when no real rows",
  "DirectApiBudget: cross-process file lock (msvcrt/fcntl) whole-transaction; records bind mission/owner/request/purpose; failed call -> RECONCILING (budget stays held, survives restart); only trusted_reconcile(request_id, actual|no-usage) settles/frees; actual>reserved forbidden; double commit refused; unknown model price REFUSED; separate cache read/write rates; conservative 3.0 chars/token upper bound"
 ],
 "EXCERPTS": {
  "gate": excerpt(engine, "REQUIRED_CAPABILITIES = (", "def _gate")[-800:] + excerpt(engine, "def _gate(", "@dataclass\nclass BenchmarkRunner"),
  "scores": excerpt(engine, "SYSTEM_IQ_WEIGHTS", "def _percentile"),
  "budget": excerpt(budget_src, "class DirectApiBudget", "class FableDirectClient")
 },
 "TEST_EVIDENCE": "benchmark gate+truth: 10 passed (smoke READY, release honestly NO-GO at HEAD, mock capability cannot cover, CLI exit 1); budget adversarial: 9 passed (5-process concurrent reserve exactly 3x1.0 of cap, restart reconciliation hold, settlement bounds, double commit, unknown model, cache-rate order)",
 "QUESTIONS": ["1) Is the gate forgery-resistant (can a manifest/runtime trick produce false READY)?", "2) Is SystemIQ/PureCodingIQ computation sound, or gameable by a lying child runtime?", "3) Is the budget fail-safe under crash between reserve and API call?", "4) Name the single highest-value improvement you would make yourself (your own idea, not from this bundle)."],
 "REQUESTED_OUTPUT": "JSON only: {\"verdict\":\"APPROVED|CHANGES_REQUIRED\",\"defects\":[{\"issue\",\"severity\",\"minimal_fix\"}],\"own_improvement\":{\"idea\",\"why_value\",\"minimal_patch_sketch\"},\"no_change_justified\":bool}"
}

result = client.run(bundle)
rid = client.reservation_id
usage = client.usage[-1] if client.usage else {}
out = {"verdict_raw": result.get("log_text", "")[:4000], "usage": usage}
json.dump(out, open(r"C:\AiMaxBossman-claude-bossman-control-v03-43igbk\docs\mission\benchmark_iq_review.json", "w"), indent=1)
print("USAGE:", json.dumps(usage))
print("VERDICT:", result.get("log_text", "")[:1800])
