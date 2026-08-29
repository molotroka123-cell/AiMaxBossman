# BOSSMAN ComputerUse — Emulator / Test-Browser Bug Check Gate

This gate is mandatory before ComputerUse implementation is marked DONE.

## Goal
Validate the browser-control layer in an isolated test browser/profile before using real authenticated websites.

## Required environment
- Chromium installed by Playwright
- isolated profile directory under `.tmp/bossman-emulator-profile/`
- local fixture pages served from `tests/fixtures/browser_emulator/`
- network to real third-party services is NOT required for this gate

## Mandatory scenarios
1. Open page and observe title/url/text.
2. Click a normal button.
3. Type into input and textarea.
4. Press Enter and keyboard shortcuts supported by policy.
5. Select dropdown option.
6. Wait for delayed DOM state.
7. Download a generated test file into agent workdir.
8. Screenshot current page.
9. Persist session state across browser restart using the same profile.
10. Keep two agents isolated: cookies/profile A must not appear in profile B.
11. Refuse unsafe click labels with `browser.click`.
12. Route unsafe action through `browser.confirmed_click` / existing approval flow.
13. Recover cleanly from missing selector, detached element, navigation timeout, and crashed page.
14. Verify no path escape on downloads.
15. Verify tool outputs are clipped/truncated according to toolkit limits.

## Completion rule
Claude MUST NOT report implementation complete when unit tests pass but emulator E2E fails.

Required final report:
- unit tests: PASS/FAIL
- emulator E2E: PASS/FAIL
- scenarios passed: N/15
- failures with exact file/test name
- screenshots/artifacts path
- known limitations

If Chromium cannot start on the current machine/CI, mark the gate BLOCKED, not PASS, and provide the exact command/error.
