# OpenHands Integration — Status After the V7 Convergence Run

**Date:** 2026-09-08
**Branch:** `night/v7-convergence-20260908`
**Supersedes:** `OPENHANDS_HONEST_STATUS.md` (same directory), which was written
before the SDK had ever been installed. Three of its claims were contradicted
the moment the package was actually there; they are corrected below rather than
quietly overwritten.

---

## What changed: the package was installed and the path was run

`openhands-sdk==1.44.1` and `openhands-tools==1.44.1` were installed into a
Python 3.12 sidecar environment and the whole path was executed:

    OpenHandsClient (real)
      -> subprocess (real)
        -> scripts/openhands_sidecar.py (real)
          -> openhands.sdk Agent / Conversation (real)
            -> file_editor / terminal tool dispatch (real)
              -> a real file in a real git worktree
    -> Bossman derives the git evidence itself (real)

The only substitution is the model's weights: a local OpenAI-compatible
endpoint (`tests/apprentice/openhands_stub_provider.py`) scripts the agent's
turns, because no provider key exists in this environment. That boundary is
stated rather than blurred — it proves the contract and the security envelope,
not that a real model chooses sensible actions.

Suite: `bossman-core/tests/apprentice/test_openhands_live_sdk.py`, 10 tests.
They SKIP rather than pass when the SDK is absent; a green run that exercised
nothing would be the exact false completion claim this document exists to
prevent.

---

## Three claims the previous report got wrong

Each was only discoverable by running the real package.

### 1. "The sidecar never prints raw model reasoning" — it did

`openhands-sdk` attaches a conversation visualizer that renders the system
prompt, every model turn and every tool result to **stdout**, plus a startup
banner. Two consequences: the JSON protocol broke (`OpenHandsClient` parses
stdout and received a system prompt), and raw model reasoning leaked into
whatever captured that stream.

Fixed in `scripts/openhands_sidecar.py`: file descriptor 1 is dup'd aside for
the single response line and then pointed at stderr before the SDK is imported,
so anything written through Python, through `rich`, or straight to the fd lands
on the diagnostic stream. The visualizer is disabled as well; the redirect is
the guarantee.

Regression: `test_the_sidecar_speaks_only_its_protocol_on_stdout`.

### 2. "GIT_EVIDENCE = PASS (derivation works)" — it dropped every new file

`git diff HEAD` shows tracked changes only. A run whose entire output was newly
created files returned `changed_files=('NOTES.md',)` next to an **empty diff**:
the file list said work happened and the evidence showed none. For a coding
worker whose main product is new files, that is the evidence gap that matters
most.

Fixed in `openhands_client._evidence_diff`: untracked paths are registered with
`--intent-to-add` so they appear in the diff. Neither the working tree nor HEAD
is touched, and the client re-checks both afterwards.

### 3. "test_worktree_isolation.py: 5/5 PASS" — 0/5 on a `master` repository

`IsolatedWorktree._get_default_branch` fell back to the literal string `main`
when there was no `origin` remote — which is the normal case for a sandbox
checkout. On any machine whose `init.defaultBranch` is `master`, both worktree
attempts referenced a branch that does not exist and the feature failed with
`fatal: invalid reference: main`. The report's "5/5 PASS" was true only on the
machine it was written on.

Two fixes: the fallback now reads the branch the repository is actually on, and
every worktree attempt creates a **new disposable branch** (`-b`) over bases
tried most- to least-specific (`origin/<base>`, `<base>`, `HEAD`). The old
fallback dropped `-b` and checked out the base branch directly, which git
refuses whenever the source repository already has it checked out — i.e. always.

All 29 tests under `tests/apprentice/` now pass.

---

## Security envelope — verified against the real SDK, not asserted

Each row is an executing test in `test_openhands_live_sdk.py`, not a claim.

| Guarantee | How it is proven |
|---|---|
| Edits outside `allowed_paths` are refused | agent really creates the file; Bossman refuses the RESULT from the real diff |
| Protected paths are refused | agent reaches for the shell (as it would in practice) and the post-run scope check catches it |
| OpenHands may not move HEAD | agent really commits; the run is rejected |
| OpenHands may not add a remote | agent really adds one; the run is rejected |
| Credentials never reach stdout or the agent's environment | key is popped before the SDK starts; the agent's own `env` dump is searched for it |
| Only OpenRouter models run | a non-OpenRouter id is refused before an agent is built — the scripted provider records zero requests |
| No invented evidence | a run that changes nothing reports changing nothing |

---

## Status matrix

```
OPENHANDS_REPO                = PASS
OPENHANDS_SDK_INSTALLED       = PASS (openhands-sdk/tools 1.44.1, Python 3.12)
OPENHANDS_REAL_E2E            = PASS (10 tests, real SDK, no Bossman mocks)
OPENHANDS_SECURITY_ENVELOPE   = PASS (7 boundaries exercised against the real SDK)
ISOLATED_WORKTREE             = PASS (5/5, now portable across default branches)
GIT_EVIDENCE                  = PASS (new files included in the diff)
SIDECAR_PROTOCOL_HYGIENE      = PASS (stdout carries one JSON line and nothing else)
LIVE_OPENHANDS_ACCEPTANCE     = NOT_RUN

OPEN_REPO_P0 = 0
OPEN_REPO_P1 = 0
```

### `LIVE_OPENHANDS_ACCEPTANCE = NOT_RUN` — the exact reason

No provider credential exists in this environment. `OPENROUTER_API_KEY`,
`LLM_API_KEY` and `bossman-core/runtime/or_key.txt` are all absent, and the
convergence run does not create one: putting a key in the repository, the logs
or a report is forbidden, and there is no key available to use even correctly.

This is a genuine external blocker, not a repository gap. Everything the
repository owns is executed and tested. To close it, an owner runs:

```bash
export BOSSMAN_OPENHANDS_PYTHON=/path/to/py312-venv/bin/python   # has openhands-sdk 1.44.1
export OPENROUTER_API_KEY=...                                    # never committed
python -m pytest bossman-core/tests/apprentice/test_openhands_live_sdk.py
```

and then repeats one harmless task against a real OpenRouter model. The
security envelope above does not change; only the model's judgement is newly
exercised.

---

## Corrections to the previous report's "External Owner Actions"

* "Install `openhands-ai`" — the package is `openhands-sdk` + `openhands-tools`,
  pinned in `bossman-core/requirements-openhands.txt`. `openhands-ai` is the
  application, not the SDK the sidecar imports.
* "Replace `run_openhands_agent()` STUB" — no such function exists. It was
  replaced before this run; the sidecar has imported and driven the real SDK
  since then. The remaining gap was never a stub, it was an uninstalled package.
