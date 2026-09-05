# Gemini handoff: virtual-only security review

This build is **not ready for real money**. The three mode flags remain
`false / true / false`; changing them fails closed and logs SECURITY_VIOLATION.
Explicit user approval is necessary for any separately scoped financial work,
but approval or a flag alone does not establish technical readiness.
The simulator must not be converted into artificial-volume generation, ranking
manipulation or concealment of coordinated trading.

- [ ] Run `python -m pytest -q solana_volume_suite/tests tests/test_solana_safety.py` from the repository root.
- [ ] Review `python tools/ci_secret_scan.py` and the scoped scan in the generated report.
      The requested `rg -n '(PRIVATE.?KEY|SECRET|MNEMONIC|SEED)' solana_volume_suite`
      is a keyword inventory: it matches safe identifiers and test fixtures too.
      Never copy real matches into reports or logs.
- [ ] Review the hypothetical test-pool Liquidity Gate PASS and invalid-order BLOCK/UNKNOWN.
      Neither is proof of an authenticated on-chain pool.
- [ ] Review Jito tip calculation. Current code is an offline heuristic;
      submission is permanently disabled and network suitability is unverified.
- [ ] Confirm Treasury Guard is active and `MAX_ALLOWED_LOSS_USD` is positive and finite.
      Simulated metrics do not demonstrate protection of real funds.
- [ ] Confirm honest dashboard status: LIVE_EXECUTION_ENABLED=NO, PAPER_TRADING=YES,
      zero confirmed transactions. A future separate live system would need its own
      verified YES/NO status; this build cannot display or activate it.
- [ ] Obtain explicit user approval of the scope and risks before any separately
      reviewed legitimate real-money work. No such approval has been obtained here.
- [ ] Review repository metadata/license and code independently before adopting
      dependencies discovered by GitHub hygiene. Stars are not a trust signal.
- [ ] Review `runtime/gemini_report.json`, its test counts, source digest, scan scope
      and limitations. Do not reinterpret missing checks as PASS.

Generate the report from the repository root:

```powershell
python solana_volume_suite/scripts/prepare_for_gemini.py
```

The bridge performs local tests and scans, reads an existing public-metadata CSV,
and writes a local report. It does not contact Solana, use real keys, alter flags,
or instruct an agent to launch mainnet.
