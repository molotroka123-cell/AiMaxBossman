# OpenHands in Bossman

## Status

**Guarded integration implemented. Live provider acceptance is NOT_RUN.**

Authoritative code commit: `b3924b65c25686560b54265d66188659908a1b24`.

OpenHands is an optional **untrusted coding worker**, not Bossman's orchestrator and not an authority that may complete missions, push, deploy, or approve its own patch.

## Architecture

```text
Bossman / TeacherFallback / budget + sanctions
        |
        v
OpenHandsTeacherClient
        |
        | sanitized ProblemBundle only
        v
disposable Git repository (NO REMOTE)
        |
        v
OpenHands SDK sidecar (Python 3.12+)
        |
        v
Claude through OpenRouter
        |
        v
candidate file changes
        |
        v
Bossman derives Git delta itself
        |
        v
existing PatchVerifier on Bossman's verifier worktree
        |
        +--> security review
        +--> acceptance tests
        +--> regression tests
        +--> evidence freshness/binding
        +--> ACCEPT / REJECT / QUARANTINE
```

The existing Claude-style `hermetic_workspace()` boundary is preserved. OpenHands does **not** replace `teacher_sandbox.py`.

## Security contract

- feature flag `BOSSMAN_OPENHANDS_CODE_FALLBACK` defaults OFF;
- only explicit `allowed_paths` may change;
- protected/out-of-scope changes fail closed;
- the real owner checkout is never passed to OpenHands;
- the OpenHands workspace is a disposable sanitized Git repository with no remotes;
- dirty workspace, pre-existing remote, HEAD rewrite/commit/reset, Git config change, or new remote is rejected;
- acceptance-test contents are not copied into the OpenHands workspace;
- OpenHands output remains `UNTRUSTED_TEACHER_OUTPUT` until the existing independent `PatchVerifier` accepts it;
- OpenHands cannot declare mission completion;
- no push/deploy authority is provided;
- the Bossman process environment is not inherited wholesale by the sidecar;
- production wiring forwards only OpenRouter provider configuration;
- `OPENROUTER_API_KEY` is pulled into the SDK `LLM` object and removed from the sidecar environment before `TerminalTool` starts;
- raw model reasoning and raw terminal history are not persisted as learning evidence.

## OpenHands runtime

Use a separate Python 3.12+ environment so Bossman's Python 3.11 compatibility is not changed.

Install matched OpenHands SDK/tool versions together:

```bash
python3.12 -m pip install -r bossman-core/requirements-openhands.txt
```

Configure Bossman:

```bash
export BOSSMAN_OPENHANDS_CODE_FALLBACK=1
export BOSSMAN_OPENHANDS_COMMAND="python3.12 scripts/openhands_sidecar.py"
export BOSSMAN_OPENHANDS_MODEL="openrouter/anthropic/<claude-model>"
export OPENROUTER_API_KEY="..."
```

On Windows, point `BOSSMAN_OPENHANDS_COMMAND` at the Python 3.12+ executable/environment that has the matched OpenHands SDK packages installed.

Do not place the API key in `BOSSMAN_OPENHANDS_COMMAND`, argv, Git, screenshots, logs, or durable evidence.

## Tests

Focused local sandbox before the authoritative code push:

- 9 subprocess/Git/client/sidecar security-contract tests PASS;
- 3 wiring/flag/OpenRouter-filter tests PASS;
- total: **12/12 PASS**.

The tests cover real subprocess boundaries, actual temporary Git repositories, out-of-scope/protected writes, dirty workspaces, remotes, environment leakage, contract tampering, hidden changes via Git commit/reset, current SDK API shape using an offline fake SDK, independent OpenRouter flag/wiring, and preservation of the old hermetic teacher sandbox.

## What is not yet proved

`LIVE_OPENHANDS_OPENROUTER = NOT_RUN` until an owner-authorized real provider call is executed with a real OpenRouter credential.

A local SDK `Conversation` uses process-level isolation. For stronger production isolation, move the same adapter to an OpenHands Agent Server / remote container workspace; the Bossman verification boundary should remain unchanged.
