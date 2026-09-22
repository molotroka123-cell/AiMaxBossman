# CRITICAL — Jev Ultrafast Browser Fast Path for Bossman

**Priority: P0 / IMPORTANT FOR OWNER TEST**

Target: `release/bossman-owner`

Upstream: https://github.com/browser-use/jev-ultrafast

This is not a replacement for Bossman's brain. It is a **System-1 browser execution fast path**: Bossman/strong models think, plan and verify; Jev handles repetitive grounded browser actions cheaply and quickly.

## Why this matters

Upstream uses a dynamic indexed action space. The browser exposes observed interactive elements, then TypeSafe Jev chooses an operation and a compatible observed target in one decision cycle. A small text model is invoked only when text must be generated.

Supported upstream action concepts include:
- CLICK
- TYPE_TEXT
- SELECT
- SCROLL_UP / SCROLL_DOWN
- WAIT
- DONE
- BLOCKED

Default loop is structured DOM/state driven rather than screenshot-driven.

Upstream reported a verified Google Flights demo around 7.1 s and a reduction in median browser protocol calls from 1,092 to 101 in a small repeated test. Treat these as upstream measurements, NOT a general Bossman performance guarantee.

## Bossman architecture

```
OWNER GOAL
   |
Bossman planner / memory / policy
   |
Browser task classifier
   |
   +--> FAST + LOW-RISK + DOM-SUPPORTED
   |       |
   |   Jev Ultrafast adapter
   |       |
   |   observed DOM -> indexed elements
   |       |
   |   Jev: operation + target
   |       |
   |   Browser Harness execution
   |       |
   |   independent Bossman verifier
   |
   +--> COMPLEX / VISUAL / UNSUPPORTED / LOW CONFIDENCE
           |
       existing full browser agent / strong multimodal model
```

The previously staged `JevDecisionProvider` is the general Bossman routing layer. This component is different: it is the browser fast-path executor. They should cooperate, not be merged into one opaque module.

## Non-negotiable upstream ideas to preserve

1. **Observed targets only.** Never allow model output to become arbitrary selectors, coordinates, JavaScript or shell commands.
2. Operation-specific target sets. A CLICK decision can only select valid click targets, etc.
3. Re-check page freshness before executing.
4. Re-check geometry/occlusion before click/input where supported.
5. Never blindly retry a browser mutation.
6. Log execution before observing its result.
7. `DONE` is NOT proof of success; Bossman independently verifies the requested outcome.
8. Credentials remain server/local-side and ignored by Git.
9. Paid API calls are excluded from offline unit tests.
10. Text-helper output must be schema validated before typing.
11. Preserve the ability to immediately fall back to Bossman's existing browser path.

## Important upstream limits

Do not assume the MVP handles every page. Upstream explicitly notes limitations around areas such as:
- shadow roots
- frames
- canvas
- uploads
- popup tabs
- nested scrolling
- arbitrary keyboard widgets

When unsupported state is detected, **escalate**, do not improvise unsafe automation.

## Proposed configuration

Keep disabled until owner setup:

```env
BOSSMAN_JEV_BROWSER_ENABLED=false
BOSSMAN_JEV_BROWSER_SHADOW=true
TYPESAFE_API_KEY=
TEXT_MODEL_API_KEY=
```

Do NOT commit real keys.

Suggested additional Bossman controls:

```env
BOSSMAN_JEV_BROWSER_MAX_STEPS=25
BOSSMAN_JEV_BROWSER_TIMEOUT_MS=30000
BOSSMAN_JEV_BROWSER_VERIFY_DONE=true
BOSSMAN_JEV_BROWSER_FALLBACK=true
```

Names may be adapted to the existing Bossman config conventions rather than creating duplicate config systems.

## Integration plan

### Phase 0 — inspect and vendor/adapt safely

- Pin a reviewed upstream commit/version rather than tracking moving `main` blindly.
- Check MIT license/notice requirements.
- Prefer a thin adapter or isolated dependency over copying unrelated demo/UI code.
- Run upstream tests before integration.
- Run dependency/security scan.
- Do not rewrite the working Bossman browser subsystem.

### Phase 1 — shadow mode

Jev receives the same low-risk browser state and proposes actions, but **cannot execute them**.

Record:
- proposed operation
- proposed target
- existing agent action
- agreement/disagreement
- latency
- API cost
- eventual task success

Use this to establish our own benchmark on Bossman workloads.

### Phase 2 — reversible low-risk execution

Allow Jev fast-path execution for tasks such as:
- navigation
- opening pages
- search/filter UI
- reading/selecting non-sensitive controls
- deterministic repetitive DOM interactions

Keep strong verification after completion.

### Phase 3 — adaptive routing

Use Bossman history to learn which task/page classes reliably benefit from Jev. Route only when measured success/latency/cost justify it.

## Approval boundary

Jev must NEVER bypass Bossman's `never / ask / allowed` policy.

Require the existing owner approval path for consequential actions, including destructive changes, purchases/payments, sending/publishing external content where policy requires approval, credential/security changes, and other irreversible external actions.

A fast executor is not an authority layer.

## Failure and escalation

Immediately fall back/escalate when:
- provider unavailable/rate-limited
- invalid schema
- stale page repeatedly
- target is not observed/compatible
- unsupported browser primitive
- task becomes visual rather than DOM-grounded
- verifier disagrees with `DONE`
- repeated no-progress loop
- risk/approval policy requires stronger handling

Do not retry mutations merely because the next observation is unexpected.

## Text model

Upstream demo currently uses an OpenRouter text helper and documents Mercury 2.5, while also describing OpenAI-compatible configuration for Gemini, GLM and DeepSeek.

For Bossman, do not hard-code Mercury. Put text generation behind the existing provider gateway so we can benchmark:
- local model on AI Max+ 395
- existing cheap cloud models
- configured fallback provider

Jev should remain useful even as our text-model stack changes.

## Local-first experiment

Because Bossman runs on Ryzen AI Max+ 395 / 128 GB unified memory, separately benchmark an open local System-1 alternative instead of assuming every reflex decision must go to cloud Jev.

Candidate for research/benchmark only:
https://github.com/APUS-AI-Lab/fast-browser-use

It describes a Jev-like local discrete-decision approach using Qwen models. **Do not replace Jev with it without our own accuracy/security benchmark.**

Goal:
```
Jev cloud fast-path  <->  local reflex fast-path
                 |
          Bossman router chooses
                 |
         strong verifier remains
```

This gives Bossman a future path toward cheap/offline browser reflexes.

## Owner acceptance benchmark

Before enabling authoritative execution, create a fixed Bossman browser suite covering at least:

1. Wikipedia/search navigation
2. Google-style search
3. multi-field form
4. dropdown/select
5. autocomplete
6. multi-page navigation
7. stale-page case
8. unsupported iframe/canvas case -> correct escalation
9. approval-required action -> MUST stop
10. intentionally wrong DONE -> verifier MUST reject

Compare:
- success rate
- median / p95 completion time
- browser protocol calls
- model calls
- input/output tokens
- estimated task cost
- fallback rate
- unnecessary actions
- verifier failures

Never promote based on speed alone.

## Tomorrow / Claude Code execution order

1. Fetch latest `release/bossman-owner`; preserve all current commits.
2. Read `docs/JEV_DECISION_ENGINE.md` and this file.
3. Inspect existing Bossman browser/provider interfaces only as deeply as needed.
4. Pin/review Jev Ultrafast upstream.
5. Implement isolated adapter behind feature flags.
6. Do not require Jev keys for normal Bossman startup.
7. Add mocked/offline tests.
8. Run shadow benchmark.
9. Produce a short evidence report with commands, results and remaining blockers.
10. Only enable execution after owner supplies keys and shadow tests pass.

## Definition of done

- Existing Bossman works unchanged with Jev disabled.
- Jev Browser can be switched on/off without rebuild.
- No secrets in repository or logs.
- Existing approval system remains authoritative.
- Browser targets are grounded in observed page state.
- Unsupported cases escalate cleanly.
- DONE is independently verified.
- Tests cover failure/fallback paths.
- We have measured Bossman-specific speed/cost/success data.
- Upstream is pinned, not silently floating.
- Rollback is immediate.
