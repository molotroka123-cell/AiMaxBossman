# Agent 03: bounded defensive security review

Initial tested SHA: `6b11330948370f9b9dee93da45748c14f89904ef`.
Gateway proxy authentication and scanner tests: **14 passed, 1 failed**, 23.32s.
The failure was the repository-clean scanner gate: two public token-mint literals
in setup_mainnet.py. This was not evidence of leaked private credentials.

Fix base: `1b953a8`. Scope: setup_mainnet.py and offline regression tests.

Confirmed dangerous source behavior: an omitted password selected a known default;
any existing-vault unlock exception deleted the vault and generated replacement
wallets. The fix requires an explicit password of at least 12 characters before
RPC access, uses getpass for interactive input, and raises on unlock failure
without deleting/replacing the original file or rewriting configuration.

Validation command:

`python -m pytest tests/test_mainnet_setup_safety.py tests/test_ci_secret_scan.py -q --tb=short`

Result: **11 passed in 18.95s** on Windows using security-venv and PYTHONUTF8=1.
The six safety cases execute the real function extracted with AST; financial
imports, vault implementation and network calls are replaced by local test doubles.
A harmless preexisting file and configuration retain their exact bytes after a
simulated unlock failure; replacement wallet generation is never called.

Limitations: no live RPC, Jito, wallet generation, financial transactions, or cloud
calls were performed. This does not attest the financial subsystem. Existing
plaintext environment-password storage and endpoint configuration deserve a
separate bounded security review; they were outside this emergency patch.
