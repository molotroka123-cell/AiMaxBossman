---
name: permission-auditor
description: Review an agent, skill, MCP server or workflow permission set for unnecessary access, privilege expansion and dangerous combinations before it is enabled.
compatibility: BOSSMAN, OpenCode, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
  category: safety
---

# Permission Auditor

Use before:
- enabling a new MCP server
- assigning new tools
- applying natural-language permission changes
- promoting a skill that expands permissions
- granting host filesystem or terminal access
- increasing browser action authority

## Goal

Apply least privilege.

The question is not:
> Could the agent use this permission?

It is:
> Does this exact workflow require this permission?

## Review categories

- filesystem read
- filesystem write
- external directories
- terminal sandbox
- host terminal
- administrator/sudo
- network
- browser navigation
- login
- upload/download
- external submit/send
- git push
- secrets
- MCP tools
- destructive actions

## Dangerous combinations

Pay special attention to combinations such as:

- arbitrary filesystem read + external network
- secrets access + browser/send
- admin terminal + untrusted external content
- MCP filesystem + unrestricted shell
- git credentials + unreviewed push
- browser login + automatic external submit

## Workflow

1. Read task/skill requirements.
2. List requested permissions.
3. Mark each:
   - REQUIRED
   - OPTIONAL
   - UNNECESSARY
   - DANGEROUS
4. Reduce permission scope:
   - narrower path
   - narrower domain
   - specific command pattern
   - specific MCP tool
5. Choose:
   - AUTO
   - ASK
   - DENY
6. Compare with previous policy.
7. Highlight every privilege increase.

## Rules

Permission expansion must never be hidden inside a skill update.

Do not solve tool failure by broadening permissions unless the access is actually required.

## Output

```text
Subject:
Required permissions:
Removed permissions:
Privilege increases:
Dangerous combinations:
Recommended AUTO:
Recommended ASK:
Recommended DENY:
Human approval required: yes/no
```
