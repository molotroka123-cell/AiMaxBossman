# V6 OpenHands Runtime

Status: **integration scaffold / feature-off by default**.

OpenHands is added as an untrusted coding executor behind Bossman's existing Apprentice/Teacher boundary. It does not replace Mission IR, approvals, evidence gates, PatchVerifier, canary/rollback, or Computer Use.

## Architecture

`Bossman (Python 3.11+) -> JSON/stdin sidecar -> OpenHands runtime (Python 3.12+) -> workspace`

After the sidecar exits, Bossman independently reads Git state and rejects protected or out-of-scope changes. The sidecar's claimed changed-file list is not trusted.

Configure the Bossman process with `BOSSMAN_OPENHANDS_COMMAND` pointing to a Python 3.12+ environment running `scripts/openhands_sidecar.py`. Configure the model in the sidecar environment with `BOSSMAN_OPENHANDS_MODEL` / provider configuration. Prefer the existing Bossman/OpenRouter credential and budget path; never place API keys in command-line arguments or durable evidence.

## Safety contract

- non-empty `allowed_paths` is mandatory;
- protected/out-of-scope writes fail closed;
- OpenHands cannot decide mission completion or bypass Bossman approvals;
- no automatic push/deploy is granted by this adapter;
- no raw chain-of-thought is persisted;
- real Git state, not agent self-report, is evidence.

## Verification

Hermetic tests use a fake JSON sidecar and a temporary real Git repository. A live OpenHands+LLM run requires owner/provider credentials and must be reported `NOT_RUN` until actually executed.

Focused test:

`cd bossman-core && python -m pytest tests/apprentice/test_openhands_client.py -q`
