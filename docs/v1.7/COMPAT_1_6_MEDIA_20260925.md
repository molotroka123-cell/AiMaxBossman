# Bossman 1.7 ↔ Bossman 1.6 media compatibility

Checked: 2026-09-25.

## Latest inspected 1.6

Branch:
`feat/bossman-1.6-bossnet-foundation-20260925`

SHA:
`c3b65e30d62383a054126a4c6726ed4a3bbf65a1`

1.6 and 1.7 are currently divergent branches. This document defines API compatibility; it is not a merge certificate.

## Existing 1.6 primitives reused by 1.7

### Telegram image ingest

1.6 already has:
- 10 MiB image limit;
- JPEG/PNG/WebP magic-byte validation;
- download after identity/size checks;
- local OpenAI-compatible vision call;
- no tools in vision request;
- history stores a note, not raw base64.

1.7 deliberately keeps the same byte/type contract.

### Bossman Studio

1.6 exposes:
- `GET /api/studio/models`
- `POST /api/studio/references`
- `POST /api/studio/jobs`
- `GET /api/studio/jobs/{id}`
- `POST /api/studio/jobs/{id}/cancel`
- `GET /api/studio/runs?job_id=...`
- `GET /api/studio/runs/{id}/file`

Studio job schema already supports:
- model
- prompt
- settings
- media references
- count

Therefore 1.7 photo editing uses this API through `StudioImageEditBroker` instead of inventing a second media service.

### Verified bytes

1.6 Studio already treats provider completion as insufficient evidence. Persisted outputs are independently verified and fetched through the verified run endpoint.

1.7 keeps that rule.

## New 1.7-only code

All current additions live under:
`command-center/bcc/pit/`

New media modules:
- qwen_vision.py
- photo_pipeline.py
- studio_image_edit.py
- photo_edit.py

No shared 1.6 Studio dispatcher/provider file is modified by this foundation.

That means current code-level merge-conflict risk is low.

## Remaining convergence item

Current 1.6 Studio catalog/dispatch does not yet register a Qwen-Image-Edit provider/model.

During AI-Max integration:
1. install/verify the local Qwen image-edit runtime;
2. register it in the existing Studio catalog;
3. add the minimal provider adapter if required;
4. keep the existing Studio reference/job/run contract unchanged;
5. run PIT photo tests + 1.6 Studio/Telegram media regressions.

Do not copy Qwen execution into PIT itself.

## Qwen source references

Pinned references are stored in:
`docs/v1.7/contracts/source-refs.json`

The implementation separates:
- local Qwen2.5-VL-style VLM for understanding;
- Qwen-Image-Edit-style model for edits.

This avoids coupling the participant protocol to one exact checkpoint name.

## Convergence rule

When 1.6 gets a new HEAD tomorrow:
- fetch it first;
- compare these API surfaces;
- adapt PIT broker only if the existing Studio contract changed;
- do not merge 1.6 wholesale into 1.7 during laptop shadow;
- later convergence happens by meaning after 1.5/1.6 tests are green.
