# Claude/Fable Limit-Saver Handoff

Read this file after `CLAUDE_START_HERE.md`.

## What has already been decided

- Browser = Playwright, DOM-first.
- Terminal Agent = Agent 07.
- Skill/MCP Agent = Agent 10.
- OpenRouter metadata/capability work = Agents 02 + 04.
- Natural-language permissions = Agent 11.
- Resource accounting for terminal/browser/OpenCode/MCP = Agent 12.
- Recovery for these runtimes = Agent 14.
- Mobile emergency controls = Agent 15.
- `.agents/skills` is the portable skill location.
- OpenCode is an execution engine under BOSSMAN.
- No unrestricted whole-computer switch.
- Sensitive actions keep AUTO/ASK/DENY.
- Existing bossman-core tool permissions should be reused, not discarded.

## Do not spend a session repeating

- OpenRouter base URL research
- whether OpenRouter is OpenAI-compatible
- whether skills are useful
- whether browser should use screenshots every step
- whether shell should have full host access
- whether MCP should have independent permission logic
- whether OpenCode should own missions

These choices are locked unless integration tests disprove them.

## Where Claude should spend tokens

1. merge these modules with the existing DB/API conventions
2. write migrations
3. unify Command Center queue with bossman-core tool loop
4. real service wiring
5. real UI
6. E2E
7. failure injection
8. security review
9. integration conflicts across the 15 agents
