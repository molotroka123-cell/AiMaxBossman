---
name: safe-terminal
description: Operate a terminal for coding and system tasks with scoped working directories, command-level permissions, visible logs, timeouts, and no silent privilege escalation.
compatibility: BOSSMAN, OpenCode, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
---

# Safe Terminal Operator

Use when an agent needs shell access.

## Default execution tiers

### Tier 1 — Project Sandbox
Default for coder agents.

- workdir mounted only
- no host filesystem outside approved roots
- network off unless task explicitly needs it
- no sudo/admin
- command timeout
- stdout/stderr logged

AUTO examples:
- git status / diff / log
- tests
- linters
- local build
- read-only package/version checks

### Tier 2 — Project Host Terminal
Use only when sandbox cannot perform the task.

ASK:
- package installation
- starting/stopping host services
- Docker operations affecting persistent services
- git push
- commands writing outside project

### Tier 3 — System Admin
Explicitly privileged.

Always ASK:
- sudo / administrator
- firewall
- drivers
- system services
- mounting disks
- user/account management

DENY by default:
- destructive disk commands
- credential dumping
- disabling endpoint security
- arbitrary deletion outside approved workspace
- force-push or destructive git history rewrites

## Whole-computer access

Never equate "terminal access" with unrestricted filesystem access.

A task must receive approved roots, for example:
- `D:/Projects/**` read/write
- `C:/Users/<user>/Documents/**` read
- system directories deny

Secrets such as `.env`, SSH keys, browser password stores and crypto-wallet data require separate explicit policy and should not be model-visible.

## Every command record

Log:
- agent
- task/run
- cwd
- exact command
- start/end
- exit code
- timeout/kill state

The dashboard must support Stop/Kill for active commands.
