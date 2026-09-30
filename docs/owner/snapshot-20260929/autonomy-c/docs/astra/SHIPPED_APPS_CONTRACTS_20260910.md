# Shipped app acceptance corrections

The baseline release already includes Social Farm and File Commander wheels.
No workflow exercised their standalone suites. The new `shipped-apps.yml`
requires the current source SHA, real Chromium on local pages and real FFmpeg;
it does not add PR59's new AI Streamer/Higgsfield subsystem or prove an account.

* SF-NEW-01, P2: a nonfinite duration (`inf`, `1e999`, finite seconds whose
  millisecond conversion overflows) raised `OverflowError`. It now remains
  unmeasured. Positive control: 1.25 seconds is measured as 1250 ms.
* SF-NEW-02, P2: negative width/height from a probe response were accepted as
  measured dimensions. Nonpositive dimensions now raise `CorruptMedia`.
  Five combined negative cases failed before these corrections.
* CI-APP-01, P2: the absent app gates allowed these boundary failures to remain
  outside Core/Command Center CI. The new gates retain their actual results.

One existing test incorrectly required ffprobe to accept a truncated PNG.
The real local executable instead returned zero dimensions and the product
correctly refused it. The corrected test records the actual tool outcome and
requires the product to refuse truncation in either case. An additional
deterministic control supplies header-only metadata and still requires the
independent structural check to refuse it. No media validation is weakened.

Local component run after correction: Social Farm 394 passed, 23 skipped
(missing local Chromium, historical specification inputs, and tests explicitly
requiring absent tools); File Commander 39 passed. These are diagnostic source
results, not final-SHA or installed-browser acceptance. CI must execute the
browser suite without skipped tests, and Windows results must be observed.
