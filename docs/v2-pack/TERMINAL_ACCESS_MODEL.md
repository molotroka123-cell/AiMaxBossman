# Terminal / Computer Access Model

Current repository behavior is intentionally restricted:

- `bossman-core` filesystem tools resolve only inside the agent workdir.
- shell sandbox uses Docker with `--network none` and mounts only the workdir.

Do not remove these protections globally.

## Agent 07 ownership

Agent 07 is upgraded from Worktree Sandboxes to:

**Workspace + Worktree + Terminal + OpenCode Runtime**

## Modes

### sandbox

Default.

- repository/worktree only
- network disabled by default
- no sudo/admin
- tests/build/git-read actions can be AUTO

### project_host

Explicitly configured host roots only.

Example:

`D:/Projects/**`

ASK:
- installs
- Docker service changes
- git push
- writing outside current project
- starting persistent host services

### system_admin

Privileged mode.

Always ASK at minimum.

Never silently elevate.

## Secrets

Do not make ordinary LLM context able to read:
- `.env`
- SSH private keys
- browser password stores
- crypto wallet files/seeds
- banking credentials
- OS credential stores

Inject secrets at the tool boundary whenever possible.

## Dashboard Terminal page

Show:
- agent
- task/run
- cwd
- exact command
- mode
- PID
- output
- start/end
- exit code

Controls:
- Stop/Kill
- approval
- Take Over / stdin where supported

A command shown in UI must map to a real process.
