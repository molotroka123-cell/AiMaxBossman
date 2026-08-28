# MCP + Skills + OpenCode integration

## Skills

Use `.agents/skills/<id>/SKILL.md` as preferred portable repository location.

Also discover compatible sources from explicitly configured roots such as:

- `.opencode/skills`
- `.claude/skills`
- user OpenCode/Claude skill folders

Never recursively scan the entire computer.

## Skill Forge

Skill Forge is allowed to propose a skill when:
- workflow repeats
- multiple agents need the same process
- user explicitly asks
- a stable checklist would reduce failures/tokens

Do not encode secrets or one-time private facts in skills.

Permission-expanding skill edits require approval.

## MCP

Use the official MCP SDK in the final runtime.

Do not hand-roll protocol framing.

BOSSMAN canonical name:

`mcp:<server-id>:<tool-name>`

Only assigned MCP tools are exposed to an agent.

AUTO / ASK / DENY remains BOSSMAN-controlled.

## OpenCode

Use `opencode serve` as a headless execution engine.

Useful server capabilities include:
- session details
- child sessions
- todo list
- session fork
- abort
- diff
- permission responses

Protect the local server with its password environment settings.

BOSSMAN stores OpenCode session IDs against its own task/run.

BOSSMAN remains canonical for mission/budget/routing/history.
