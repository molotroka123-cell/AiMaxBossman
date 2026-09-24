# CODEX OWNER RUN — Bossman 1.5 economy closure — 2026-09-25

You are the final local integrator. **Do not spend Codex context doing work that Bossman workers can do.** Drive Bossman, inspect evidence, accept/reject patches, and close only reproducible blockers.

## 0. Source truth

1. `git fetch --all --prune`.
2. Bossman 1.0 is frozen/certified on its own exact-SHA line. Never push 1.5 work into that line.
3. Work only from the current `feat/bossman-1.5-economy-orchestrator-20260924` HEAD.
4. Read `docs/v1.5/ECONOMY_ORCHESTRATOR.md`, `docs/v1.5/ACCEPTANCE_AND_LEARNING.md`, and this file.
5. No force-push and no new final-final branches.

## 1. Required Bossman workers

Use the existing owner OpenRouter key through Bossman's secret path/environment. Never print or copy it into Git.

- Nemotron Extractor: `nvidia/nemotron-3-ultra-550b-a55b:free`
- Nemotron Skeptic: `nvidia/nemotron-3-ultra-550b-a55b:free`
- Nemotron Curriculum: `nvidia/nemotron-3-ultra-550b-a55b:free`
- Free coder/tester: `inclusionai/ling-3.0-flash-fin:free`
- Paid finalizer: `z-ai/glm-5.3-flash`
- Jev: existing Bossman TypeSafe/DefAPI integration
- Aster: audit/general coordination only

GLM hard run cap = **$0.50 total, maximum 4 calls**. No auto-recharge. Unknown cost = stop paid lane. Jev routes roles but cannot approve spending or external effects.

## 2. First prove code before model calls

```powershell
python -m pytest -q tests/test_v15_economy_orchestrator.py
python -m pytest -q command-center/tests/test_jev_decision.py command-center/tests/test_jev_feature.py
python -m pytest -q command-center/tests/test_video_learning_primitives.py
python -m pytest -q tests/test_distill_recorder.py
python tools/v15_economy_orchestrator.py plan
```

If deterministic code is red: send the bounded failing test/error to **Ling through Bossman first**. Ling gets at most two attempts. Then one Nemotron critique. Codex writes the patch only after those free attempts fail or for a security/release boundary.

## 3. K1m6a YouTube training window

Target: **2026-08-14 through 2026-08-27**.

Default source identity is K1m6a / `UC2KGf4oWao2NMOnIA88ZwJQ`. If the owner's original K1m6a URL is available locally, pass that exact URL as `--source-url`; otherwise use the configured channel URL and record the source identity rather than guessing.

```powershell
python tools/youtube_trader_ingest_batch.py discover `
  --from-date 2026-08-14 --to-date 2026-08-27 `
  --out "$env:LOCALAPPDATA\Bossman\CommandCenter\youtube-window.json"

python tools/youtube_trader_ingest_batch.py ingest `
  --manifest "$env:LOCALAPPDATA\Bossman\CommandCenter\youtube-window.json" `
  --output-root "$env:LOCALAPPDATA\Bossman\CommandCenter\youtube-training"
```

Do not send raw video frames, owner memory, cookies, secrets or credentials to free cloud workers. Cloud workers receive only the compact public evidence digest.

## 4. Jev-managed free swarm

Every video must receive all three independent Nemotron passes:

1. Extractor — claims, explicit levels, triggers, invalidations, 15/30/60m hypotheses.
2. Skeptic — lookahead/hindsight/unit/series/causality attack.
3. Curriculum — candidate skills/workflows/memory plus unseen transfer tests.

Jev controls their order and retry/escalation policy. It never promotes a lesson, never changes `never/ask/allowed`, and never grants payment authority.

Then Ling independently verifies evidence and produces executable tests, bounded code tasks, rejected claims and lesson candidates. Hidden verifier — not model prose — decides PASS.

## 5. Codex token-saving rule

`FAILURE -> Bossman/Ling attempt -> tests -> Nemotron critique if needed -> tests -> Codex reviews compact evidence`

Codex writes code itself only when the free workers failed twice on the same bounded defect or a deterministic security/release judgment is required.

Never ask Codex to reread full transcripts, raw video dumps or huge logs. Give it the failing test, compact verifier report and candidate diff.

## 6. Paid GLM final pass

Use GLM only after free workers finish, and only if one is true:

- Ling verifier is FAIL;
- free workers materially disagree;
- a P0/P1 remains;
- target tests pass but independent verifier rejects the candidate.

Run the final pass with `--allow-paid-finalizer`. Stop at policy cap. Record exact OpenRouter-reported cost. Unknown cost means STOP. GLM output is a proposal, not PASS; deterministic tests must pass after any fix.

## 7. Aster

Aster is **audit/general coordination only**:

- compare 1.5 with frozen 1.0;
- verify no authority, budget, privacy or trading-boundary regression;
- audit evidence and hidden tests;
- identify P0/P1/P2;
- never implement a fix and then certify its own fix as independent evidence.

## 8. Learning

Raw YouTube material is `UNVERIFIED`.

`UNVERIFIED -> OUTCOME_LABELLED -> independent verifier -> candidate skill/workflow/memory -> unseen transfer -> PROMOTED`

Record BEFORE/AFTER on unseen tasks. Teacher patch != student success. Weights remain unchanged unless a separate explicit training job is run and documented.

## 9. 1.5 acceptance tomorrow

Run:

- free fleet probes;
- Ling hidden coding scenarios;
- Jev route/fallback/STOP negative controls;
- restart/resume;
- private/secret egress negatives;
- GLM cost-cap negative control;
- YouTube quarantine/promotion negatives;
- existing 20-class 1.5 acceptance wherever dependencies are available;
- installed Windows bundle smoke for the economy runner.

Write `docs/owner/BOSSMAN_1_5_OWNER_RUN_2026-09-25.md` with exact SHA, model IDs, free/paid call counts, dollars spent, tests, promoted lessons, unseen transfer, P0/P1/P2, OWNER_REQUIRED items and `weights_changed`.

Do not claim `BOSSMAN_1_5_RELEASE_CERTIFIED` from docs, mocks, model prose or a teacher-only PASS.
