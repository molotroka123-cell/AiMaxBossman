# Routine code added to save Claude tokens

This pack now includes extra boilerplate that Claude should **reuse**, not rewrite:

1. `command-center/bcc/v2/tables.py`
   - centralized SQLAlchemy declarations for most new V2 entities

2. `openrouter_catalog_service.py`
   - sync remote OpenRouter catalog
   - stale marking
   - catalog search
   - pin remote model into BOSSMAN registry

3. `tool_messages.py`
   - preserves OpenAI-compatible `tool_calls`
   - parses JSON function arguments

4. `capability_probe.py`
   - real chat/tools/structured-output probes

5. `schemas.py`
   - Pydantic request objects for the new APIs

6. `tests/v2/fake_provider_app.py`
   - deterministic local OpenAI-compatible provider
   - `/v1/models`
   - tool calling
   - structured output

7. `tests/v2/browser_fixture.html`
   - deterministic Playwright E2E page

8. `tools/apply_v2_scaffold.py`
   - dry-run copier
   - copies only NEW files
   - never overwrites repository files

## Claude instruction

Before hand-writing any of these routine pieces, inspect the prepared implementation.

Spend model time on:
- safe integration
- migrations
- canonical runtime unification
- UI
- E2E
- failure recovery
- security
instead of retyping table declarations and test fixtures.
