# Jeff temporary mood control — 2026-10-02

## Change

The owner-only Jeff settings page now offers `Сердитый — 24 часа` for the shared
default style. It sets directness to 10/10 and warmth/humour to 1/10. Its bounded
instruction directs frustration at the problem and keeps threats, humiliation,
discrimination, harassment and calm crisis handling explicitly out of scope.
It changes no permission, privacy, identity, memory or tool policy. The preset is
not available in an individual participant editor.

The settings API accepts a strict `style_duration_hours` value from 0 to 24.
Positive values save a canonical UTC expiry; 0 clears the expiry. Expired shared
style is ignored on the next overlay read, without a background timer. Individual
participant overrides are retained. Reset removes the expiry and style while
preserving the existing budget behavior.

## Verification

Command-center focused regressions, isolated temporary data/configuration and
installed Windows Chromium:

```text
python -m pytest -q tests/test_jeff_settings_overlay.py tests/test_jeff_2_safety.py tests/test_jeff_audit_fixes.py
293 passed, 0 failed, 12 deprecation warnings
```

Coverage includes settings validation/roundtrip, API ownership, automatic expiry,
manual reset, participant-override retention, real local Chromium selection/save,
and existing Jeff safety regressions. An initial UI regression caught a test race
that read the settings file before the second PUT completed; the test now waits
for the successful response and passes on rerun.

`git diff --check` passed before recording this note. These are repository-local
tests on the feature branch. They do not prove the live Jeff process loaded the
code or that the owner switched the setting in the live Command Center.

## Live status

No live mood configuration was changed. The owner UI helper failed to initialize
(`helper_unknown_error: setup refresh had errors`), and the active installation's
source identity is still unknown. Applying this overlay directly to its data file
could make an older build reject the settings and fall back to stock, so the live
flip remains unverified. No Telegram messages were sent; the intended recipient
set for “everyone” has not been identified.
