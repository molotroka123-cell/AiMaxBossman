# Bossman 1.7 — Personal Identity Training

Experimental branch for a separate Telegram assistant with local-first model routing and per-user personalization.

## Product contract

Each Telegram participant starts with zero personal context. The conversational model receives only:
- generic PIT assistant rules;
- the current participant's own relevant memories;
- that participant's recent turns/files;
- public web evidence when freshness is needed.

The participant chat never receives the machine owner's personal profile, global Bossman memory, another Telegram participant's profile, or owner-console context.

Telegram participants never receive desktop-control, shell, owner-admin, payment, trading, evolution-control or secret-management tools.

Jev selects the model/tool path. Laptop bootstrap uses owner-configured remote models. After migration to AI Max the same runtime switches to LOCAL-FIRST AUTO: routine local model selection and ordinary web search do not require per-message owner confirmation.

## Collection-first

The first experiment optimizes recall rather than perfect memory filtering:
1. answer the participant;
2. extract many allowed memory candidates;
3. attach provenance/confidence/time/category;
4. record later outcomes;
5. later train a separate sorter to KEEP / DROP / TTL / MERGE / SUPERSEDE;
6. evaluate the sorter on different users/held-out turns.

Obvious credentials/secrets are never retained as persona memory. Sensitive long-term categories remain separately controlled.

## Existing Bossman reuse

PIT is not a second Bossman backend. Reuse:
- Command Center backend;
- Telegram transport/inbox/idempotency;
- provider/model registry;
- Jev;
- existing web adapters;
- cost governor;
- existing secret storage.

PIT adds only a participant-only policy surface, PersonaVault namespace, personalized retrieval, collection/outcome data and discovery-question logic.

## Code already in this branch

- `command-center/bcc/pit/identity.py` — HMAC pseudonymous per-ID key.
- `models.py` — consent and memory candidate contracts.
- `vault.py` — isolated per-participant storage/export/delete.
- `collector.py` — high-recall candidate intake + future sorter labels.
- `context.py` / `participant_context.py` — bounded own-person retrieval and zero-start system context.
- `router.py` — local/zero-cost/paid policy routing.
- `policy.py` / `companion_profile.py` — participant-only tools/commands.
- `discovery.py` — at most one useful optional personalization question.
- `telegram_contract.py` — onboarding/idempotency/user commands.
- `command-center/tests/test_pit_foundation.py` — P0 foundation contracts.
- `.github/workflows/v17-pit-ci.yml` — isolated PIT gate.

## Docs

- [Завтра начать отсюда](START_TOMORROW_GLM_RU.md)
- [Jeff public behavior/privacy](JEFF_PUBLIC_BEHAVIOR_RU.md)

- [Full PIT specification](PERSONAL_IDENTITY_TRAINING_SPEC_RU.md)
- [Laptop remote run](LAPTOP_REMOTE_RUN_RU.md)
- [Dataset / future garbage sorter](DATASET_AND_GARBAGE_SORTER_RU.md)
- [GLM-5.3 implementation master](GLM_5_3_IMPLEMENTATION_MASTER_RU.md)
- [OSS + datasets shortlist](OPEN_SOURCE_AND_DATASETS_20260925.md)
- [Pinned source revisions](contracts/source-refs.json)
- [Persona schema](contracts/persona.schema.json)
- [Memory candidate schema](contracts/memory-candidate.schema.json)
- [Jev route schema](contracts/jev-route.schema.json)

## Tomorrow target

Status name: `PIT_LAPTOP_SHADOW_READY`.

Required sequence:
1. Run PIT foundation tests.
2. Inspect/reuse existing `bcc.telegram_companion`; do not build another Telegram stack.
3. Add a dedicated PIT bot/profile that filters owner commands before dispatch.
4. Wire PersonaVault by current participant only.
5. Wire GLM-5.3/allowed zero-cost remote routing for the laptop.
6. Wire web freshness path.
7. Wire memory commands and high-recall extraction.
8. Run two synthetic participant IDs and prove no cross-profile contamination.
9. Run owner-only real Telegram shadow.
10. Save evidence report.
11. Only then invite one participant, later 3–4 total.

The laptop success is not local-model proof. After the first successful laptop answer the same bot/profile moves to AI Max and repeats the same acceptance with local models enabled and prioritized.

This branch remains isolated from Bossman 1.5/1.6 until a separate convergence pass.
