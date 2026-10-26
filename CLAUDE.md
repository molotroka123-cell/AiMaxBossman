# Mandatory startup for Claude / Argon — AiMaxBossman

Read [AGENTS.md](AGENTS.md) first. Follow its owner approval / privacy / STOP / RunPod rules and all existing engineering gates.

**Main goal (owner 26.10.2026, AGENTS.md §0):** bring Bossman as far as possible, by the strongest PROVEN means, to the product the owner planned — one local-first Bossman, an operator of the owner's PC, a Higgsfield-level local studio, a stack chosen by measurement. `main` receives only ONE unified finished Bossman version, merged overnight by the Claude integrator under the audit conditions in AGENTS.md §0.

## Owner directive for 23 October 2026: verified 2x performance program

**Required read-before-code**: [docs/owner/BOSSMAN_2X_PERFORMANCE_NORTH_STAR_20261023_RU.md](docs/owner/BOSSMAN_2X_PERFORMANCE_NORTH_STAR_20261023_RU.md).

The user wants a **measured target** of >=2.00x median end-to-end improvement on a preregistered owner-PC coding/agent workload, with no loss of quality, security, permissions or verified outcomes. Start with the P0 trace and baseline: iPhone -> Mobile Web Pult -> Command Center -> Agent Router -> LLM/Tools -> independent verification. Then test optional Vulkan MTP, prompt/KV caching, skill fast paths, serial API overhead and existing Resource Governor under isolated A/B benchmarks.

A historical 2.09x Qwen decode result is not proof that Bossman overall is 2x faster. **Never mark success without end-to-end results** on an exact installed SHA, independent tests, before/after raw metrics and owner acceptance.

Read the 23 October North Star audit and skill-proof receipts, do not assume old green statuses are current. Push evidence checkpoints CP-PERF-00..06 to a work branch and request approval before merging behavioral code to main. These documentation instructions do not permit autonomous production upgrades, costly providers, personal-data uploads, RunPod or permission bypass.

If the agent has only been handed this file, **do not claim the owner machine has been tested**. Ask for owner-PC execution only when necessary; do all noninteractive and local evidence steps first.

## Owner rule 23.10: features are proven, not wrapped
Read the AGENTS.md section «A feature is PROVEN, not wrapped». A capability is "added" only with unit tests, a live
proof on the owner's PC through the real pult/chat path with a receipt under `docs/audits/`, and that receipt in the
merge commit. Anything the owner can see broken is a class-1 red and pre-empts other work. Chat attachments (images,
scans, pdf/docx/xlsx/pptx) must reach the model as content — proof: `tools/prove_chat_attachments.py`. A chat photo
must be editable by the agent (refined prompt → local Qwen edit via `photo.edit`) — proof: `tools/prove_chat_photo_edit.py`.
