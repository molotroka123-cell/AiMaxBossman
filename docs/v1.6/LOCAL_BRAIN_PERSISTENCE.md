# Bossman 1.6 — Local Brain Persistence

Status: **NON-NEGOTIABLE PRODUCT INVARIANT**

## One sentence

**Bossman learns locally; installing a new Bossman version changes the program,
not the owner's accumulated intelligence. The owner brain stays on the machine
and is never pushed to GitHub.**

## Boundary

Canonical owner runtime state lives under the external
`BOSSMAN_DATA_DIR`, outside every repository checkout and worktree.

The repository may contain:
- source code;
- schemas/migrations;
- synthetic fixtures;
- explicitly reviewed public/test datasets;
- redacted aggregate evidence.

The repository must never contain the owner's:
- memories or personal facts;
- runtime LearningStore;
- learned/private skills or agent histories;
- Personal Operating Graph runtime database;
- routing/model-performance history;
- self-improvement checkpoints;
- PIT/personality records;
- local brain backups.

## Version lifecycle

### Upgrade

`old binary + local brain -> snapshot/manifest -> install new binary -> migrate or
attach same data root -> verify -> continue learning`

Do not initialize an empty canonical brain when a valid existing brain is found.

### Reinstall

Program files may be replaced completely. Owner brain remains intact by default.

### Uninstall

Uninstalling program code is not consent to erase memory. A destructive brain
wipe is a separate explicit owner action.

### Rollback

Never overwrite a newer canonical brain with older bundled data. If a previous
binary cannot safely understand a newer schema, fail read-only/blocked and
restore only from an explicit local backup workflow.

## GitHub rule

`RUNTIME_BRAIN -> GIT` is always **DENY**.

This includes commits, PRs, issues/attachments, release artifacts and automated
evidence uploads. Only hashes/counts/redacted summaries may leave the local
brain for release evidence.

## Tomorrow acceptance

During the 1.5 -> 1.6 -> 1.7 run:

1. learn/store one verified lesson before an upgrade;
2. store one project decision/fact;
3. record one agent/skill/routing history item;
4. replace/install the next Bossman version;
5. restart;
6. prove all applicable records are still retrievable;
7. prove Git index/status and Windows release ZIP contain no private brain data.

Required verdicts:

```
BRAIN_DATA_ROOT_EXTERNAL=PASS
BRAIN_SURVIVES_UPDATE=PASS
BRAIN_SURVIVES_RESTART=PASS
BRAIN_GIT_LEAKS=0
BRAIN_RELEASE_ARTIFACT_LEAKS=0
```

Any private brain pushed to GitHub is P0.
Any normal update/reinstall that loses learned owner state is P1 and blocks
release freeze.
