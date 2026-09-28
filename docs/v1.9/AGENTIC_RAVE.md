# Agentic Rave (Bossman 1.9, workstream G)

One prompt, several AI agents, each in its own isolated copy of the project.
The owner watches them side by side in Bossman CMD and keeps pause / resume /
STOP for every agent and for the whole rave. Nothing an agent does can reach
the owner's project without the normal Bossman approval.

Status: owner GREEN LIGHT 2026-09-28 (пульт); `rc19/g-rave` merged into
`rc19/p-green` for the lead's integration into PR #84.

## 1. Bossman CMD (the `bossman` terminal)

```
bossman rave "<prompt>" --agents mock:a,mock:b,local:qwen [--repo PATH] [--allow PATH]... [--test "CMD"] [--detach]
bossman rave list
bossman rave status <id> [--watch]            # table: agent · provider/model · status · step · files · tests · answer/error
bossman rave show   <id> <agent>              # full answer, error, tests output, changed files
bossman rave diff   <id> <agent>              # git diff of the agent's isolated workspace vs the rave base
bossman rave conflicts <id>                   # files changed by 2+ agents, where every version is kept
bossman rave pause  <id> [--agent X]          # one agent, or all agents of the rave
bossman rave resume <id> [--agent X]
bossman rave stop   <id> [--agent X]          # STOP one / all agents of this rave
bossman rave stop --all                       # STOP every rave
bossman rave apply  <id> <agent> [--approval-id N]   # owner-approved copy into the project
```

The same commands are available inside `bossman chat` as `/rave …`. All of them
are thin clients of the one Command Center backend (`/api/rave/*`); the terminal
keeps no state of its own (Terminal Run 1.2 contract). `--json` gives machine
output. `bossman stop --all` (owner global STOP) also stops every rave.

Agent spec: `<connector>:<name>[@<model>][?key=value&key=value]`

| connector | what runs | pause | notes |
|---|---|---|---|
| `mock` | deterministic scripted agent in-process (`steps`, `delay`, `file`, `crash_at`) | at step boundary | for tests and demos; never claims to be a model |
| `local` | Bossman's own local tool loop `bossman.apprentice.local_sidecar` (OpenAI-compatible endpoint, Ollama by default) as a child process | process tree suspended | model = `@model` or the Bossman local coding default |
| `claude` | official Claude Code CLI `claude -p` (headless, documented) in the agent workspace | process tree suspended | owner opt-in via a Bossman approval; owner's own login; see §7 |
| `codex` | official Codex CLI `codex exec` in the agent workspace | process tree suspended | BLOCKED for a ChatGPT-subscription login (see §7); same interface |

## 2. Data model (durable, `<BCC_DATA_DIR>/rave/<rave_id>/`)

```
rave.json            # the record (atomic write: tmp + os.replace)
events.jsonl         # append-only timeline shown by `status --watch`
agents/<name>/ws/    # the agent's isolated git clone (its only writable place)
agents/<name>/exec.log   # one line per executed/reconciled step (proof of no duplicates)
conflicts/<n>/       # base / agent-A / agent-B / merged-with-markers for each conflicting file
base/                # scratch project, when no --repo is given
```

Rave: `id, prompt, repo, base_commit, scratch, test_cmd, allow, created_at, boot_id, agents[], conflicts[], applied{}`.
Agent: `name, connector, provider, model, spec, status, step, steps_total, journal[], answer, error,
workspace, branch, result_commit, changed_files, diff_stat, tests{ran, passed, exit_code, output_tail},
started_at, finished_at, pause_reason`.

Agent status: `queued → running ⇄ paused → done | failed | stopped | blocked | interrupted`.
Rave status is derived from its agents (`running`, `paused`, `done`, `partial`, `stopped`).

## 3. Isolation

* Every agent gets its own `git clone` of the project at a pinned base commit
  (`base_commit`), with the `origin` remote removed and its own branch
  `rave/<id>/<agent>`. A clone (not a shared-ref `git worktree`) is used so an
  agent CLI running `git` inside its workspace can not move or delete branches of
  the owner's repository. Objects are hard-linked by git, so a clone is cheap.
* The connector process runs with `cwd` = that workspace. The local sidecar
  confines every read/write to it (and to `--allow` paths); Claude Code runs with
  `--permission-mode acceptEdits` + an explicit tool allow-list with `Bash`/web
  denied, so a write outside the working directory needs a permission nobody can
  grant in `-p` and is denied; Codex runs with `--sandbox workspace-write`.
* When an agent ends (done, failed, stopped or crashed) Bossman — not the agent —
  commits the workspace (`git add -A; git commit`, author BOSSMAN) so its work is
  a durable commit. Workspaces are never deleted automatically.
* Uncommitted changes in the owner's project are not visible to agents (they get
  the committed base); `rave status` shows the base commit.

## 4. Approvals — no authority expansion

* An agent can only change its own workspace. The ONLY path from a workspace to
  the owner's project is `bossman rave apply <id> <agent>`: it creates a normal
  Bossman approval (`kind=rave_apply`, preview = rave, agent, repo, base,
  result commit, file list). The owner decides with `bossman approve N`
  (terminal, web or Telegram); the apply then `consume()`s exactly that approval
  (anti-replay, bound to the preview). Files are written to the working tree
  only — no commit, no push.
* Apply refuses (409 CONFLICT, nothing written) when a target file in the
  project no longer equals the base version — e.g. another agent's version was
  applied first. Both versions stay in their workspaces and in `conflicts/`.
* Using a subscription CLI (`claude`) needs a one-time owner opt-in, which is a
  normal Bossman approval (`kind=rave_connector_optin`). Until it is approved the
  agent is `blocked` with the approval id; the rest of the rave runs.
* `--test "CMD"` is the owner's own command, run by Bossman in each finished
  workspace (argv, no shell, timeout, process tree killed, provider keys removed
  from the environment). It is not an OS sandbox.

## 5. STOP / pause semantics

* `stop` (agent or rave): durable `stop_requested` first, then the connector's
  whole process tree is killed (Windows Job object) and the runner task
  cancelled; the agent ends `stopped`, its partial work is committed and kept.
* `bossman stop --all` (the owner global STOP) emits `owner.stop_all`; the rave
  feature listens and stops every running rave agent.
* `pause`: in-process agents stop at the next step boundary; child-process agents
  are suspended immediately (every process of the tree, psutil). `resume` continues
  from the same point. Paused time counts against the agent's own timeout.
* A crash of one agent (exception, non-zero exit, malformed output) marks only
  that agent `failed` with the error; the other agents are unaffected.

## 6. Crash recovery (backend restart mid-rave)

* Each step is journaled: `started` before the effect, `done` after it.
* On startup the feature loads every rave; an agent left `running`/`paused` by a
  previous boot is set `paused` with `pause_reason=recovered_after_restart` — it is
  never re-run silently. `bossman rave resume <id>` continues it:
  * completed steps are skipped (journal);
  * a step that was `started` but not `done` is reconciled: the mock connector
    checks whether its effect is already present (then records `reconciled`, no
    re-execution); a non-idempotent step (a CLI/model turn) is marked
    `interrupted` with outcome UNKNOWN and is re-run only on an explicit
    `rave resume <id> --agent X` (owner decision) on top of the kept workspace.
* `exec.log` per agent proves each step's effect ran once.

## 7. Subscription connectors (Codex CLI, Claude Code CLI)

Only the official CLIs, only their documented non-interactive modes, only the
owner's own login done by the owner (Bossman never reads, copies or refreshes
their credentials; it only runs each CLI's own status command, keeping
`loggedIn`/`authMethod`/`subscriptionType`, never e-mail or org ids).

* **Claude Code** — `claude -p` (headless, documented: https://code.claude.com/docs/en/headless).
  Anthropic's legal page (https://code.claude.com/docs/en/legal-and-compliance)
  says usage limits for Pro/Max "assume ordinary, individual usage of Claude Code
  and the Agent SDK" and that the restrictions do not "prevent an end user from
  signing in to the unmodified Claude Code binary with their own Claude
  subscription"; it forbids third-party developers from offering Claude.ai login
  or routing requests through Free/Pro/Max credentials *on behalf of their
  users*, and from collecting/intermediating tokens. Bossman is the owner's
  personal tool on the owner's machine running the unmodified binary under the
  owner's own login, so this is SUPPORTED — personal, individual use only, behind
  the owner opt-in. If Bossman is ever offered to other people, each user must
  sign in with their own account and the Commercial Terms apply.
  Invocation: prompt on stdin (never through `cmd.exe` argv), `--output-format json`,
  `--permission-mode acceptEdits`, `--allowedTools Read,Edit,Write,Glob,Grep`,
  `--disallowedTools Bash,WebFetch,WebSearch`, `--setting-sources project`,
  `--no-session-persistence`, cwd = agent workspace.
* **Codex CLI** — `codex exec` is the documented non-interactive mode
  (https://learn.chatgpt.com/docs/non-interactive-mode). The official Codex pricing
  page (https://learn.chatgpt.com/docs/pricing) lists "Codex SDK, `codex exec`, and
  scriptable workflows" as included in ChatGPT Plus, Pro, Business and
  Enterprise/Education plans (not Free/Go). The auth page
  (https://learn.chatgpt.com/docs/auth) *recommends* API keys for CI/CD and warns
  against exposing Codex execution in untrusted/public environments; the owner's
  own machine, attended, is neither. So a ChatGPT-subscription login is
  SUPPORTED behind the owner opt-in. Invocation: `codex exec --sandbox
  workspace-write --skip-git-repo-check --ephemeral --json -o <file> -C <workspace> -`
  (prompt on stdin).
* Owner rule (lead clarification): both CLIs run through the owner's
  SUBSCRIPTION login. `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` and
  `OPENAI_API_KEY`/`CODEX_API_KEY` are removed from the child environment so a
  stray key never silently switches billing; a CLI logged in with an API key is
  `blocked` ("не по подписке") unless the server sets
  `BOSSMAN_RAVE_ALLOW_API_KEY=1` AND the agent says `auth=api_key` (off by
  default, labelled "API key (paid per token)" in status).
* Status shows each agent's auth mode ("subscription (claude login)" /
  "subscription (codex login)"); a CLI that is not logged in blocks only that
  agent and shows the exact manual step (`claude auth login --claudeai`,
  `codex login` → Sign in with ChatGPT). `bossman rave connectors` shows all of
  it plus the sources above.
* A stub CLI with the same interface (tests/rave_stub_cli.py) is used by the tests.

## 8. What is borrowed

* Git isolation per agent (the pattern used by Claude Code `--worktree`, Codex,
  and parallel-agent runners), with Bossman's own `IsolatedWorktree` choice of a
  remote-less clone; `git merge-file --diff3` for the conflict artifacts.
* Bossman's `ProcessTree` (Windows Job object kill), `local_sidecar` tool loop,
  `approvals.create/consume`, `allowed_roots`, the event bus and the 1.2 terminal
  client. No second task engine, memory or model registry.
