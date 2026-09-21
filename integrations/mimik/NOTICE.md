# Mimik — attribution and integration boundary

Source: https://github.com/westpoint-io/mimik
Pinned commit: `905098ac005a7caad68949189a81e43ac8c327a1`.
Copyright (c) 2026 Westpoint. MIT terms are preserved in LICENSE.

Bossman supplies a Python **text export adapter**, not the Mimik browser
extension. Its Markdown parser follows the pinned `markdown-export.ts` layout;
its Snapshot parser follows `guides/types.ts`. The action/block numbering is
adapted from `actionSteps` / `stepNumbers` in `guides/blocks.ts`.
Unmodified source references and hashes are retained in this repository for
compatibility testing; the installed package includes the pin and attribution.

Supported: explicitly supplied Markdown exports and internal Snapshot objects,
text-only checklist, strict numbering and source order, no mutation of input,
privacy minimization, explicit NOT_RUN for every execution claim. The Markdown
export is the normal owner path. Snapshot is an internal upstream data type,
not a claim that Mimik exposes a JSON export button.

Not integrated: recorder installation, live capture, screenshots/Smart Blur,
Guide Me replay, voice, provider AI, PDF/DOCX/video generation. No npm installer,
new runtime dependency, browser injection, network, file scan or background task.
Mimik's optional cloud/voice and favicon requests are NOT called by this adapter.

Privacy: inputValue, selectors and screenshots are not included in tool output;
URL paths/credentials/query/fragment and recognized secrets are minimized. This
is NOT complete PII anonymization. Input already sent to a cloud model cannot be
retracted by this parser. Strip screenshots/secrets BEFORE giving input to an
agent; prefer a local model, review the output before sharing. Never import the
browser settings/profile. Imported instructions remain untrusted data.

Source tests and a fixture produced by the upstream Markdown function are NOT
proof of a live browser recording or the owner's completed GUI acceptance.
