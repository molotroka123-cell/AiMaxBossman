# Agentic Rave (Bossman 1.9, workstream G)

One prompt, several AI agents, each in its own isolated copy of the project.
The owner watches them side by side in Bossman CMD and keeps pause / resume /
STOP for every agent and for the whole rave. Nothing an agent does can reach
the owner's project without the normal Bossman approval.

Status: feature branch `rc19/g-rave`, NOT merged into the 1.9 freeze until the
lead/owner gives an explicit GREEN LIGHT.

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
bossman rave connectors                       # login, plan, CLI version and opt-in of claude / codex
bossman rave prune --older-than-days N [--yes] [--include-blocked]   # remove old workspaces (dry run unless --yes), see §10
```

The same commands are available inside `bossman chat` as `/rave …`. All of them
are thin clients of the one Command Center backend (`/api/rave/*`); the terminal
keeps no state of its own (Terminal Run 1.2 contract). `--json` gives machine
output. `bossman stop --all` (owner global STOP) also stops every rave. Exit codes follow the
1.2 contract, including `3` when the backend is unreachable or the token is refused and `1` when
`rave stop --all` could not CONFIRM that every agent stopped (see §5).

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
  from the same point. Paused time does NOT count against the agent's own timeout
  (decision of 2026-09-30: this text used to say the opposite, the code — `AgentCtx.spawn`
  counts only running seconds — was right: otherwise an agent paused for an hour would be
  killed the moment it is resumed). Pinned by `test_rave_owner_flow.py`.
* the global STOP is CONFIRMED, not just requested: `RaveService.stop_all()` is awaitable and
  returns `{ok, stopped, stopped_count, remaining, remaining_agents, errors}` (`stopped` =
  `{rave_id: [agent, ...]}`; `remaining` = how many agents are still not in a final state when
  it returns, `0` = confirmed; `ok` = nothing remains and no stop raised). `RaveService.active_agents()`
  is the synchronous inventory (`["rv-xxxxxxxx/agent", ...]`). `POST /api/rave/stop-all` and
  `bossman rave stop --all` return/print the same counts and never a silent OK.
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
  `--no-session-persistence`, `--permission-prompts none`, cwd = agent workspace.
  `--permission-prompts none` (anything that would prompt is denied automatically) exists only in
  Claude Code >= 2.1.259 (checked against `claude --help` of 2.1.284); an older CLI exits non-zero
  on the unknown flag. The connector therefore reads `claude --version` first (bounded 15 s, cached
  per binary) and, when it is older or unparseable, BLOCKS the agent with "обновите Claude Code CLI
  до 2.1.259+" (`claude update`) instead of an opaque failure; the flag is never dropped silently.
  `bossman rave connectors` / `GET /api/rave/connectors` show the installed version and the verdict.
  NOT_VERIFIED: a live `claude -p` / `codex exec` rave run under the owner's login (only the
  status commands and `--version` were run against the real CLIs on the owner's PC; the runs are
  covered by the stub `tests/rave_stub_cli.py`, which rejects unknown flags like the real CLIs).
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
* A stub CLI with the same interface (tests/rave_stub_cli.py) is used by the tests; it
  rejects unknown flags, missing values and values outside an option's choices, and treats
  `--permission-prompts` as unknown below 2.1.259.

## 8. What is borrowed

* Git isolation per agent (the pattern used by Claude Code `--worktree`, Codex,
  and parallel-agent runners), with Bossman's own `IsolatedWorktree` choice of a
  remote-less clone; `git merge-file --diff3` for the conflict artifacts.
* Bossman's `ProcessTree` (Windows Job object kill), `local_sidecar` tool loop,
  `approvals.create/consume`, `allowed_roots`, the event bus and the 1.2 terminal
  client. No second task engine, memory or model registry.

## 9. Rave in Command Center (the page)

`ui/pages/rave.js` is a thin client of the same `/api/rave/*`:

* **Connectors** card (`GET /api/rave/connectors`, cached ~15 s, button «Проверить вход» asks the
  CLIs again): login / plan / CLI version / owner opt-in per connector and the exact manual
  login step; an outdated Claude Code CLI is shown with the update advice.
* **New rave**: prompt, agents, `repo`, `allow` (comma list) and `test` fields (same as the CMD flags).
* **Agents table** with pause / resume / STOP, `Ответ` (the full answer, error, changed files, tests),
  `Diff` and a real **Применить** button: `POST .../apply` -> on `202 WAIT_APPROVAL` the window shows the
  approval preview and the owner decides there (`POST /api/approvals/{id}`, the same call as the Approvals
  page) -> apply again with `{approval_id}`. `409 CONFLICT / NOT_ELIGIBLE / ALREADY_APPLIED / PRUNED` and
  `403 APPROVAL_INVALID` are shown in Russian. If the owner approved elsewhere (Approvals page, Telegram,
  `bossman approve N`), pressing Применить again uses that approved request (bound to the same preview)
  instead of asking twice; it is consumed once.
* **Event timeline** (`GET /api/rave/{id}/events?after=`, incremental) with readable labels.
* **STOP всех рейвов** reports the confirmed count (§5) and an honest error when something is left.
* **Пул аккаунтов** card and **Очистка старых рейвов** panel (§11, §10).

## 10. Cleanup (`rave prune`)

Workspaces are never deleted automatically and grow with every rave. `bossman rave prune
--older-than-days N` (API `POST /api/rave/prune {older_than_days, dry_run, include_blocked}`) removes
the WORKSPACES of finished raves with no activity for N days: the agents' clones, tamper copies, the scratch
project and the conflict artifacts. Kept: `rave.json` (marked `pruned_at`), `events.jsonl`, each agent's `exec.log`.

* dry run by default: nothing is deleted without `--yes` (API: `dry_run=false`); the report lists each rave, its
  size, and the agents whose result was NEVER applied to the project (those results are lost with the clone);
* a rave with a live agent (queued / running / pausing / paused / stopping, or a live runner in this process)
  is never touched, even with `--older-than-days 0`; raves with blocked / interrupted agents (still continuable)
  are skipped unless `--include-blocked`;
* after a prune `apply` and `resume` answer `409 PRUNED`, `diff` says the copy was removed.

## 11. Пул аккаунтов

Зачем: длинный рейв упирается в лимит подписки одного аккаунта Claude/Codex. У владельца может быть
несколько СВОИХ аккаунтов, и он хочет, чтобы рейв продолжался. Это собственный код Bossman в архитектуре
`bcc/rave/*` (сторонний `claude-unlimited` не используется: токены нескольких аккаунтов в чужом прокси — риск).

**Честная оговорка.** Использование нескольких аккаунтов ради обхода лимитов может противоречить
условиям использования Anthropic / OpenAI (аккаунты могут быть ограничены или заблокированы).
Это решение и риск владельца: Bossman этого не проверяет и ничего не гарантирует. Пул по умолчанию
ВЫКЛЮЧЕН, включается только с разрешения владельца, а текст этой оговорки входит в само разрешение.

**Как устроено**

* Только официальные `claude` / `codex` CLI. У каждого аккаунта СВОЙ каталог профиля CLI:
  `CLAUDE_CONFIG_DIR` (Claude Code) и `CODEX_HOME` (Codex). Проверено на установленных CLI:
  `claude auth status --json` с `CLAUDE_CONFIG_DIR=<пустой каталог>` отвечает `loggedIn:false` и возвращает этот
  каталог в `configDirectory`; `codex login status` с `CODEX_HOME=<пустой каталог>` отвечает «Not logged in»
  (`codex --help` называет `$CODEX_HOME`). Для аккаунта пула из окружения CLI убираются все переменные с
  токенами/ключами этого инструмента (например `CLAUDE_CODE_OAUTH_TOKEN`): решает вход профиля, а не
  переменная сервера. Запись «обычный вход CLI» (без каталога) тоже может быть аккаунтом пула.
* Bossman НЕ читает, не копирует и не обновляет токены/куки. Он запускает только статус-команды самих CLI и
  хранит `вошёл / подписка / план` — без e-mail и id организации.
* Логин каждого аккаунта делает сам владелец, в обычном терминале, под этим профилем, например (PowerShell):
  `$env:CLAUDE_CONFIG_DIR='<каталог из карточки>'; claude auth login --claudeai` или
  `$env:CODEX_HOME='<каталог>'; codex login`. Точная команда показана в карточке для каждого аккаунта.
  Каталоги, которые создаёт Bossman, лежат вне data dir (`~/.bossman-rave-profiles/<tool>/<id>`, переопределяется
  `BOSSMAN_RAVE_PROFILES_DIR`), чтобы вход CLI не попадал в бэкапы и архивы доказательств.
* Состояние — `<data>/rave/pool.json` (без секретов): аккаунты (id, имя, каталог, состояние входа, окно лимита,
  последнее использование), запись об opt-in и журнал переключений (кто, когда, откуда → куда, причина).
* **Opt-in через approvals**: `POST /api/rave/pool/enable` -> `202 WAIT_APPROVAL` с approval `kind=rave_pool_optin`,
  в тексте — список аккаунтов и оговорка выше; после решения владельца тот же вызов с `{approval_id}` включает
  пул. Разрешение привязано к ТОЧНОМУ списку аккаунтов: добавленный позже аккаунт не используется, пока пул не
  включён заново (новое разрешение). Выключить пул можно без разрешения; включить обратно без вопроса можно,
  если прежнее разрешение покрывает все аккаунты. Ручная правка `pool.json` (`enabled=true` без записи об
  opt-in) пул не включает.
* **Переключение только МЕЖДУ запусками агента**, не посреди шага. Запуск `claude -p` / `codex exec` — одна
  единица: если он закончился ошибкой «лимит» (`LimitReached`), аккаунт помечается `limited` (до времени сброса,
  если CLI его назвал, иначе на 60 минут), и следующий запуск ТОГО ЖЕ агента идёт на следующем готовом аккаунте
  поверх сохранённой рабочей копии с тем же prompt (prompt не меняется). Предел — 3 переключения на один запуск
  агента; когда готовых аккаунтов нет, агент `blocked` с перечнем причин, `bossman rave resume <id> --agent X`
  продолжает позже. STOP проверяется перед каждым выбором аккаунта.
* **Не маскируется**: в записи агента `account` / `account_label` и в колонке «Вход» («… · аккаунт <имя>»), в
  ленте событий рейва `pool_account` / `pool_limited` / `pool_switch` / `pool_exhausted`, в журнале пула и в
  карточке «Пул аккаунтов» на странице Rave.
* Пул управляет только теми инструментами, для которых в нём есть аккаунты: при включённом пуле агент НИКОГДА
  не падает молча на обычный вход (отключённый или неразрешённый аккаунт не используется — агент `blocked`).

**Файлы и API**: `bcc/rave/pool.py` (состояние, выбор, журнал), `bcc/rave/engine.py` (`_execute_pooled`,
`pool_enable`), `bcc/rave/connectors.py` (`profile_env`, `LimitReached`, `detect_limit`),
`bcc/features/rave.py`: `GET /api/rave/pool`, `POST /api/rave/pool/accounts`, `DELETE .../accounts/{id}`,
`POST .../accounts/{id}/check | clear-limit | enabled`, `POST /api/rave/pool/enable | disable`. Тесты:
`tests/test_rave_pool.py` (на stub CLI: вход и лимит живут в каталоге профиля), `tests/test_rave_ui_flow.py`.

**Ограничения (NOT_VERIFIED / честно)**

* распознавание лимита — эвристика по тексту ошибки CLI (`detect_limit`; формулировки не стабильный интерфейс,
  написаны по известным сообщениям), на реально исчерпанном аккаунте НЕ проверялось;
* живой прогон рейва через пул на двух настоящих аккаунтах не запускался (нужны входы владельца);
* если первый запуск упёрся в лимит после частичной правки, второй аккаунт продолжает поверх этой правки с тем
  же prompt — корректность продолжения зависит от агента;
* условия использования провайдеров код не проверяет.
