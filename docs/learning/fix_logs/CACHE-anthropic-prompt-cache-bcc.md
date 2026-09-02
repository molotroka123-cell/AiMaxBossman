# Learning Case: CACHE-anthropic-prompt-cache-bcc

## Metadata
MODEL: claude-fable-5-1
AGENT: fable-lead
START_SHA: 4d46f5a
END_SHA: HEAD+1
LEARNING_STATUS: UNVERIFIED
OUTCOME: PARTIAL
VERIFIED_BY: pytest:command-center/tests/test_providers.py
CONFIDENCE: 0.6
TAGS: {"domain": "efficiency", "bug_class": "missing_instrumentation", "component": "bcc.providers", "severity": "INFO"}
FINDINGS: 

## Task
Real Anthropic prompt caching in command-center AnthropicAdapter with measured instrumentation

## Symptom
Direct Anthropic adapter sent system as a plain string and tools without cache_control; cache_read/creation tokens were dropped, so no cache savings could be measured (gateway had caching only via OpenRouter).

## Reproduction
- tests/test_providers.py::test_anthropic_prompt_cache_prefix_and_measured_usage (payload shape + usage parsing)

## Evidence
- pre-fix payload: system str, tools without cache_control, usage cache_* ignored
- post-fix: system=[{type:text, cache_control}] before messages; last tool carries cache_control; ChatResult.cache_read_tokens/cache_write_tokens from usage; engine logs model.prompt_cache with measured counts

## Hypotheses considered
- adapter predates caching support (root cause)

## Root cause
No cache breakpoints on the stable prefix and no usage instrumentation in the direct adapter.

## Relevant code paths
- command-center/bcc/providers.py:AnthropicAdapter.chat
- command-center/bcc/providers.py:ChatResult
- command-center/bcc/engine.py (model.prompt_cache log)

## Fix strategy
Stable prefix (tools, system) marked with cache_control (ephemeral; 1h via BCC_ANTHROPIC_CACHE_TTL); messages appended after; usage cache_read_input_tokens/cache_creation_input_tokens surfaced; tokens_in = input + cache_read + cache_write; usage shape unchanged when no cache measured.

## Alternatives considered
- cache breakpoint on the last user message (volatile; would churn cache)
- claiming estimated savings from prefix size (rejected: not a measurement)

## Why this fix was chosen
Prefix stability is what the cache keys on; measurement comes only from provider usage.

## Files changed
- command-center/bcc/providers.py
- command-center/bcc/engine.py

## Tests added
- tests/test_providers.py::test_anthropic_prompt_cache_prefix_and_measured_usage
- tests/test_providers.py::test_anthropic_prompt_cache_can_be_disabled

## Original reproduction after fix
payload shaped as required; measured usage parsed

## Adversarial variants
- cache disabled via env → plain system, no cache_control
- no tools → only system breakpoint

## Regression
tests/test_providers.py 9 passed; tool loop + v2 core 21 passed

## Fresh external verification
NOT measured: no Anthropic API key on this host — live cache_read_input_tokens>0 not observed

## Generalizable lessons
- Cache savings are a provider measurement (cache_read_input_tokens), never a prefix-size estimate
- Keep the volatile part after the cacheable prefix; put the breakpoint on the last stable element

## Teach local model
- Recognize: system as str and tools without cache_control in an Anthropic payload
- Verify using: usage.cache_read_input_tokens > 0 on the second identical-prefix call

## Limitations / follow-up
- cost model still prices cache-read tokens at full input price (conservative)
- live hit rate unmeasured on this host
