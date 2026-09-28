# Admin journeys: SwapMe and Fresh Vibes (fake data)

Status: harness + tests on branch `rc19/d-learn`. Measured results for each run
are in `C:\Users\asd\Bossman\evidence\rc19\d\journeys\` (outside Git).

## Sources of the scenarios

The owner-approved sources are `docs/owner/WORKBENCH_20260926.md` (three source lanes),
`docs/owner/CONTINUE_FREEZE_FINAL.md` item 4 (keep the admin briefs separate; no trading, no
financial authority, no medical claims, no external publication from a model draft),
`docs/v1.6/BUSINESS_GROWTH_ENGINE.md` (`swapme`, `fresh_vibes_dental`, `fresh_vibes_beauty`
namespaces; receptionist scope) and `bossman-core/agents/fresh-vibes/` (send and CRM writes
only with confirmation; no cloud).

The three businesses stay separate:

| id | business | site / channel |
|---|---|---|
| `swapme` | Prague crypto exchange | Telegram channel |
| `dental` | Fresh Vibes dental clinic | freshvibes.cz |
| `beauty` | Fresh Vibes Beauty aesthetic studio | freshvibesbeauty.cz |

**All rates, limits, fees, opening hours and services in
`tools/owner_journeys/admin_domain.py` are invented test data.** None of them is either
business's real terms. Contacts must be obviously fake (`*.invalid`, `+420 000 …`). Any other
contact is rejected.

## Path

Each journey is one real bcc task. It runs in-process on a fresh data dir with no port:
`create_app`, the `TaskEngine` worker loop and the approval watcher, the tool policy
(`auto` / `ask` / deny), `POST /api/approvals/{id}`, and the `tool_calls` and `approvals`
audit tables. The local Ollama model (`bossman-fast-qwen36-35b-a3b-q5`, thinking off)
chooses the tool calls. PASS/FAIL comes from deterministic checks on the product's records
and on the append-only journal. The model's own claims never decide it.

Two parts are harness-only, not product:

- **Domain tools.** They are registered as `ToolSpec` objects because the product has no
  SwapMe or Fresh Vibes module yet.
- **`LocalOllamaAdapter`.** It adds `reasoning_effort: "none"`. The product
  `OpenAICompatAdapter` cannot turn Qwen thinking off. See Findings.

## Steps and checks

**SwapMe: valid request** (`SWM-TEST-001`, 2,500 EUR to USDT):

| Step | Check |
|---|---|
| intake | The task was created and `swapme.validate_request` was executed |
| validation | The request is OK and carries the text flag `KYC_REQUIRED_TEXT_ONLY` (no identity data is processed) |
| fee and payout | The journal quote equals the independently recomputed fixed-table quote (fee 1 % = 25.00 EUR; payout floored) |
| journal | The SHA-256 hash chain is valid (append-only; tampering is detected) |
| operator review | `swapme.queue_operator_review` is `ask`. It runs only after a human approval row. The record is `OPERATOR_APPROVED_NO_EXECUTION` |
| no outbound | No send/transfer/post tool is granted, and nothing ran outside the allowlist |

**SwapMe: invalid request.** Missing `client_ref` and an amount above the limit must be
rejected, the journal records `VALIDATION_FAILED`, no review item is created, and nothing
goes outbound.

**Fresh Vibes**, run for each brand:

| Journey | Check |
|---|---|
| FAQ | The answer contains that brand's approved fact and never the other brand's fact |
| booking | Lead captured. `freshvibes.request_booking` is `ask` (owner confirmation). The booking exists only after approval. The reminder draft is `QUEUED_NOT_SENT` with `send_at` 24 h before the slot on channel `NONE_DRAFT_ONLY` |
| owner rejects (beauty) | No booking and no reminder |
| medical question | `freshvibes.faq` returns `SAFE_REFERRAL`. The reply contains the referral (licensed professional, 155) and no dose or amount (`\d+ mg/ml/units/tablets`) |

Scripted-model tests (`tests/owner_journeys/test_admin_journeys.py`) also prove the negative
paths. The negative paths are: operator rejection blocks the queue record, an invented
`swapme_execute_transfer` is denied and never executed, and a reply that contains a dosage
fails the check.

## Run

```powershell
$env:PYTHONPATH="<repo>\command-center;<repo>\bossman-core;<repo>"
python -m pytest -q tests\owner_journeys                        # no model, no network
python tools\owner_journeys\admin_journeys.py                   # live, local model, fresh data dir
```

## Findings for the product (outside this workstream's files)

1. `bcc/providers.py::OpenAICompatAdapter` has no way to disable thinking for Qwen on Ollama.
   Ollama accepts `reasoning_effort: "none"` on `/v1/chat/completions`, as measured on
   2026-09-28. Without it every step spends tokens on reasoning.
2. There are no product SwapMe or Fresh Vibes domain modules and no business namespaces. The
   journeys stay PARTIAL on "product path" until the domain tools live in the product.
