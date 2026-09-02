# AUDIT_STATE — AUDIT-ONLY-001 (mission ledger + run logs)

REMOTE_SHA=b8a26dce6bcf8ec45bc904f96dfaccaf6ac88644 (=EXPECTED ✓, delta audit not needed)

## Budget ledger (durable: budget.json, cap $3.00, reserve $0.30 untouched)
| request_id | model | in/out | cost | purpose |
|---|---|---|---|---|
| req_011CefJ9UHa3q9UpzRggzGDM | claude-sonnet-4-5-20250929 | 3843/1600 (cache 0/0) | $0.035529 | consolidated security audit (durable/deep_fix/promotion/cache + benchmark math + capability priority) |
ACTUAL=$0.035529 · REMAINING=$2.964471 · stop-threshold $2.70 never approached.

## HOW TO RE-RUN (next session, one command)
1. set key: `$env:ANTHROPIC_API_KEY = "<fresh key from owner>"` (NEVER commit it)
2. run: `& "$env:LOCALAPPDATA\Temp\opencode\venvs\py311\Scripts\python.exe" docs\mission\AUDIT-ONLY-001\run_audit.py`
   - script self-contained: builds bundle from current HEAD, reserves via DirectApiBudget (durable, cross-process safe), calls direct API, writes full log to `docs\mission\AUDIT-ONLY-001\fable_consolidated.json` (usage + verdict_raw)
   - double-commit/reconciliation is automatic; unknown model => REFUSED
3. read verdict: `python -c "import json;print(json.load(open(r'docs\mission\AUDIT-ONLY-001\fable_consolidated.json',encoding='utf-8'))['verdict_raw'])"`

## Confirmed findings (Fable, for GLM verification next session)
- F3-DURABLE-TX-SILENT-FAIL (P0): `durable.py _tx` rollback failure silenced (`except sqlite3.Error: pass`) → partial-write window can undermine nonce-once. Fix plan: raise TransactionPoisonedError, poison/close connection, integrity check.
- F2 (P1) promotion GC guard · F4-PROMOTION-NO-BASELINE (P1): `promotion.advance` security snapshots Optional → gate skippable. Fix: mandatory params + PromotionError.
- F5-PROMOTION-CROSS-CORPUS (P1): no corpus_id binding on Candidate/SecuritySnapshot → cross-corpus promotion poisoning path.
- F6-CACHE-FALSE-SAVINGS (P2): waste detector savings accounting.
- Benchmark math: Fable's formula critique + capability priority (top-6) — full text in fable_consolidated.json.
GLM triage pending next session (F3/F4 need local repro tests before CONFIRMED; both have exact code paths).

## Local evidence already collected
secret scan PASS · CI 4/4 green at b8a26dc · prior adversarial suites green (47+9+11) · trading-learning module: NOT_IMPLEMENTED (no files) · uncommitted changes: only docs/mission artifacts.

## Scores (provisional, pending F3/F4 confirmation)
SECURITY 7.5/10 · UCA 8/10 · MEMORY/CONTEXT/REASONING 6.5/10 · BENCHMARK 7/10 (2/18 capabilities) · OVERALL 7/10, PRODUCTION_READY=NO-GO for release gate until capability coverage + P0-F3 closed.
