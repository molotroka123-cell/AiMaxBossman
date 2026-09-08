# AI Streamer — what is implemented, and what only the owner's machine can prove

This maps the design pack in this directory onto code and tests that exist in
this branch. It is a map, not a claim of live readiness: every deterministic
test in this repository runs against fixture pages, and a capability proven on
a fixture stays `EXPERIMENTAL` by construction
(`apps/social-farm/src/social_farm/browser/capabilities.py`). Nothing here can
promote it.

## Implemented

| Design requirement | Where it lives | What holds it |
| --- | --- | --- |
| Browser generation runs through the account session (identity, challenge, audit, isolation, capability ledger) | `generation/higgsfield_adapter.py` | `tests/unit/test_higgsfield_session_adapter.py` |
| Versioned selector pack, semantic strategies first | `generation/higgsfield_selectors.py` | `browser/selectors.py::check_strategy_order` |
| CAPTCHA / login / MFA / quota / rate limit stop the job and call the owner | `higgsfield_adapter.prepare/poll` | `test_higgsfield_session_adapter.py` (5 cases) |
| A click is not proof of submission | `higgsfield_adapter._submission_signals` | one-signal negative control |
| Quarantine → measure → promote, extension from the measurement | `generation/media_gate.py`, `generation/workspace.py` | `tests/unit/test_higgsfield_collect.py` |
| Per-account quarantine, 0700, owner marker, cross-account refusal | `generation/workspace.py` | same file |
| Durable job store, submission receipt written before the wait | `generation/job_store.py` | `tests/unit/test_generation_durability.py` |
| One worker per job (lease with an expiry, O_EXCL file lock) | `job_store.claim/renew/release` | same file |
| Restart resumes at polling; a finished job is not resubmitted | `browser_worker._await_result` | same file |
| Bounded, backing-off polling; download retried once, invalid media not | `browser_worker` | same file |
| Routing from measured facts; unmeasured closes a route | `generation/route_inputs.py` | `tests/unit/test_generation_route_inputs.py` |
| Local → cheap cloud → browser chain, evergreen fallback | `route_inputs.plan_generation_route` | same file |
| World-state observations published without inventing values | `command-center/bcc/reality/media_routing.py` | `command-center/tests/test_v7_generation_observations.py` |
| Versioned persona canon, locked facts, observations never rewrite it | `generation/persona.py` | `tests/unit/test_generation_persona_pipeline.py` |
| script → shots → prompts, closed-list prompt assembly | `generation/pipeline.py` | same file |
| Continuity QA, segment manifest for Video Studio | `generation/segment_qa.py` | same file |
| UI-drift packet built by subtraction, secret-shape refusal | `generation/drift_report.py` | `tests/unit/test_ui_drift_packet.py` |
| OpenHands repair mission: scoped paths, protected safety layer, diff admissibility | `bossman-core/bossman/apprentice/selector_repair.py` | `bossman-core/tests/test_ui_drift_repair.py` |
| Six-hour unattended run, `deadlock_count = 0` | `generation/soak.py` | `tests/unit/test_generation_soak.py` |

### Deterministic fixtures required by `HIGGSFIELD_BROWSER_WORKER.md`

| Fixture | Test |
| --- | --- |
| authenticated READY page | `test_a_ready_page_becomes_ready_only_after_identity_is_verified` |
| login required | `test_a_sign_in_page_asks_the_owner_and_types_nothing` |
| human challenge | `test_a_captcha_stops_the_job_and_hands_the_session_to_the_owner` |
| missing submit control / UI drift | `test_a_missing_generate_control_is_ui_drift_not_a_retry` |
| successful submit | `test_a_submission_is_accepted_only_with_two_independent_signals` |
| no observable submission after click | `test_a_click_alone_is_not_proof_and_is_never_repeated` |
| provider failure message | `test_a_provider_failure_is_reported_as_a_failure` |
| rate limit | `test_a_rate_limit_is_reported_not_worked_around` |
| output ready | `test_a_ready_result_is_seen_without_pressing_anything` |
| download failure | `test_a_download_that_never_arrives_is_reported_not_waited_forever` |
| invalid downloaded media | `test_an_error_page_named_mp4_is_not_media` |
| timeout | `test_a_provider_that_never_finishes_hits_the_polling_ceiling` |
| selector repair | `bossman-core/tests/test_ui_drift_repair.py` |
| secret redaction | `test_the_packet_carries_no_markup_at_all` and neighbours |
| duplicate submission prevention | `test_the_same_job_is_never_submitted_twice` |
| worker restart with an in-flight receipt | `test_an_in_flight_job_resumes_at_polling_after_a_restart` |

## Deliberately not done here

**Video Studio import is a file handoff, not a cross-service call.**
`segment_qa.SegmentManifest.write()` writes the manifest into the approved
workspace; Video Studio picks it up through its existing `/media` and
`/commands` endpoints. Social Farm does not import `bcc`, and the static scan
in `tests/unit/test_independence.py` keeps it that way — a direct call would
turn an independent service into a coupled module.

**Publishing and streaming.** Nothing in this branch publishes. A segment
manifest is a file on the owner's disk; the existing permission and approval
path is what lets anything out.

**Capability promotion.** No test here can raise a browser capability above
`EXPERIMENTAL`. That needs `real_browser` evidence, and there is nobody in this
environment to issue it.

## Still requires the owner's machine

These are not gaps in the code; they are claims this environment cannot make.

1. **A real authenticated Higgsfield session.** There is no account here, and
   creating one for a test is outside the application's boundary.
2. **The current selector pack.** `higgsfield_selectors.py` is version
   `0.1.0-unverified` and says so in its name. Its strategies were written
   against the documented UI, not observed on it. First live run will need
   `generation.prompt.fill`, `generation.submit`, `generation.job_card` and
   `generation.result.download` checked against the real page — and the
   repair path in this branch exists precisely for that.
3. **A real download.** Media validation is proven on real PNG and real
   ffmpeg-built MP4 files, but not on a file Higgsfield produced.
4. **`ffprobe` on the owner's machine.** Without it, video cannot be measured,
   and unmeasured media is refused (`UNMEASURED`) rather than accepted. That is
   the intended behaviour, and it means video generation needs ffmpeg installed.
5. **Local media model resource profile.** `route_inputs` refuses the local
   route until free memory is measured; nothing here has measured a real local
   image/video model's footprint.
6. **Six hours of real time.** The soak harness runs six virtual hours in
   milliseconds and six real hours with `--real-time`. Only the second one
   observes real memory, real provider behaviour and real drift.

Repository tests are not live acceptance, and this branch does not convert one
into the other.
