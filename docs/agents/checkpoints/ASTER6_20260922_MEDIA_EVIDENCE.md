# ASTER6 bounded media evidence — 2026-09-22

Tested integration: `f2718aec2e5b35707073c3073d84265f6660d728`.
Own worktree: `C:/Users/asd/Bossman/wt-aster6`.
Windows, Python 3.12, pytest 9.1.1, own `.venv`; editable root + command-center[dev] from this worktree. Import path verified as this worktree's `command-center/bcc/__init__.py`.
FFmpeg n8.1-11-g75d37c499d-20260430 binaries borrowed read-only from the existing app's media directory; no installed-product validation claimed. No owner data, GPU, live model, Telegram transport, or running service used. All engine results below are MOCK_ENGINE.

## Reproduction (PowerShell, own worktree root)

```powershell
$env:PATH='C:\Users\asd\Bossman\app\BOSSMAN-Windows-x64-0c1cbe651f52\media;'+$env:PATH
./.venv/Scripts/python.exe -m pytest command-center/tests/aster6_repro_media.py -q -s --tb=short --disable-warnings
```

Compact output (exit 1):

```text
{'status': 'completed', 'verdict': 'PASS', 'declared_s': 10.0625, 'observed_s': 1.938, 'engine': 'MOCK_ENGINE'}
{'explicit_timeout_s': 60.0, 'effective_timeout_s': 60.0}
{'explicit_timeout_s': 3660.0, 'effective_timeout_s': 33629.85871271585}
FAILED test_short_decodable_output_must_not_complete_ten_second_request
AssertionError: Wrong frame count/duration accepted as completed
assert 'completed' == 'failed'
FAILED test_explicit_timeout_override_wins_even_when_equal_to_catalog[3660.0]
assert 33629.85871271585 == 3660.0
2 failed, 7 passed in 2.12s
```

Existing suites (same PATH and interpreter):

```text
python -m pytest command-center/tests/test_studio_sdcpp_provider.py command-center/tests/telegram_contracts/test_companion.py -q --tb=short --disable-warnings
90 passed in 17.62s

python -m pytest command-center/tests/test_studio_sdcpp_hostile.py -q -k 'cancel_before or cancel_while or cancel_during_run or hard_timeout or reconcile' --tb=short --disable-warnings
10 passed, 56 deselected in 5.12s
```

Initial harness setup errors, resolved before measurement: global Python had no pytest; local dependency path was corrected from nonexistent `bossman-shared` to the repository root; fixture imports were corrected to package-relative imports. Neither counted as a product defect.

No patch to product or existing tests. Reproducers intentionally retain failing assertions and must be invoked explicitly. Existing tests' successful short-output expectation is preserved for the integrator to reconcile with this independent evidence.
