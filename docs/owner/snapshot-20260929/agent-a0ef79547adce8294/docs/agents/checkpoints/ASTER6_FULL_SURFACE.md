# Aster 6 Full-Surface Checkpoints

Append only material new checkpoints here. If neither code nor risk evidence changed, stay silent.

## 2026-09-22 11:01 -07:00

BASE_SHA: 5416fdbe1ee96eb2cd59f7541f69214ac70f2d1e (initial empty checkpoint, not a prior PASS)
HEAD_SHA: f2718aec2e5b35707073c3073d84265f6660d728 (integration, tested detached in own worktree)

CHANGED_SCOPE:
- Base -> integration: documentation only; no product-code delta. First bounded samples: sd.cpp duration/deadline and Telegram companion identity/replay/approval contracts.
- Remote fetched before and after tests; unchanged: release f8dbaee3f5ce5a62607ec2ff9a063eaa2ff4f080; Codex 833a6a2ad0ad066ab3389910a13c0112fa23720d; Aster base above.

VERDICT: FAIL (sampled media invariants; not a full-surface verdict)

NEW_FINDINGS:

P0: none demonstrated.

P1: ASTER6-MEDIA-001 — wrong duration/frame count accepted as completed/PASS.
- Request `length=10s` resolves to two 81-frame segments at 16 fps, declared 10.0625 s. Existing MOCK_ENGINE emits 16 frames per segment; actual concatenation is 31 frames / 1.938 s. API returns `status=completed`, `studio.verdict=PASS` and publishes a run.
- Root: `sdcpp._run_engine` checks decodability, `fetch` records declared/observed durations without comparing them, and `runtime.persist` compares dimensions only. Same verification functions execute for real engines; no live/fake branch exempts the fixture from duration validation. No verification functions or fake flags were modified in the reproducer.
- Existing `test_ten_second_chain_is_two_segments_joined_without_the_repeated_frame` explicitly expects successful 1.9375 s output for the 10 s request. It proves concat mechanics, not preset output validity.
- Impact: a short/truncated but decodable engine result becomes successful owner output. Required invariant: validate segment frames/fps and final joined duration against the stored plane before completion/publication.

P2: ASTER6-MEDIA-002 — explicit timeout equal to catalog default silently overridden.
- Set `provider.hard_timeout_s=3660.0`, submit 1280x704 / 81 frames / 50 steps: effective timeout is 33629.85871271585 s. Negative control `60.0` stays 60.0.
- Root: `sdcpp.submit` detects explicit override using value inequality with `_catalog_timeout_s`; explicit value equal to default is indistinguishable from unset. Contradicts d059ac60's explicit override priority claim. Workaround: a distinct timeout value; preserve explicit/unset state in the fix.
- Both provider checks cancel before engine spawn; no nine-hour execution occurred.

MODEL_PERFORMANCE: not measured.

BLOCKERS:
- MEDIA-001 blocks declaring this sampled media path correct. Installed ZIP, GPU generation, and owner hardware NOT_RUN; no new claim about their state.

EVIDENCE:
- `command-center/tests/aster6_repro_media.py` — opt-in failing tests (not default `test_*.py` discovery); 2 failed, 7 passed. Failures retained, no xfail/expected-result weakening.
- `docs/agents/checkpoints/ASTER6_20260922_MEDIA_EVIDENCE.md` — commands, environment, compact failing output.
- Existing provider + Telegram companion suites: 90 passed. Targeted media cancel/timeout/orphan reconciliation: 10 passed, 56 deselected. These do not prove installed/live PASS.
- Insane dimensions/frames/steps/segments rejected in six negative controls. Catalog bounds make current deadline finite; largest accepted 30s plan budgets 101.7268 h. No separate absolute ceiling found in scaling code; no extra severity assigned without an agreed ceiling.
- Product code unchanged; branch code/catalog/test baseline identical to audited integration HEAD.

OVERLAP:
- Claude already covers: presets, stored-plane fix, proportional deadline (d059ac60), concat mechanics. New evidence challenges output-validity and override claims, not those implementations' existence.
- Codex already covers: checkpoint at 833a6a2a is an empty ledger; no substantive PASS/FAIL to contradict.

RECOMMENDATION: Claude should enforce duration/frame/fps validation before publish, preserve explicit timeout priority, and turn these reproducers into passing regressions.

## 2026-09-22 11:39 -07:00

BASE_SHA: f09c6065584da71b0f0e779874cd578d5b3dc3ce (previous audit commit)
HEAD_SHA: 8f8d06073a71517bd1eca2a2106eef8e3326c07e (remote integration, reference only)

CHANGED_SCOPE: Owner-requested 10-minute live coding assessment with a 5-minute solution budget; not a repeated code audit.

VERDICT: PARTIAL — Bossman API login verified; UI and model assessment NOT_RUN.

NEW_FINDINGS:
P0: none established.
P1: none newly established.
P2: none newly established.

MODEL_PERFORMANCE: NOT_RUN, no response/TTFT/generation-speed/intelligence score.

BLOCKERS:
- Browser automation exposed no browser; opening IAB failed.
- Both running Bossman model registries returned zero models; no listeners on 8081/8082/8083.
- Claude actively renewed exclusive GPU/desktop/model-server lease at 18:34:29 UTC. Transfer was requested from owner and remained unresolved; no takeover attempted.

EVIDENCE:
- `docs/agents/checkpoints/ASTER6_20260922_TIMED_CODING.md` — timings, API login/logout, source identity and environmental evidence.
- `docs/agents/puzzles/ASTER6_CIRCULAR_20260922.md` and `aster6_circular_verify.py` — prepared challenge and independent 11,658-case verifier, not a submitted/evaluated model answer. Five oracle example checks and syntax check passed.
- Runtime identity differs from integration: 8810 = d059ac60 checkout; 8800 = fc266856 checkout. Packaged interpreter is not evidence of an isolated installed build.

OVERLAP:
- Claude already covers: exclusive live machine resources; no interference.
- Codex already covers: no model result claimed or contradicted.

RECOMMENDATION: Arrange a free GPU/model lease and a usable UI session, then execute a fresh 5-minute attempt without treating this blocked session as an intelligence failure.
