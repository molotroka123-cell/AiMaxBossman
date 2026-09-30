# Bossman 1.7 — AI Max photo / vision / edit pipeline

## Scope

Photos are intentionally AI-Max-only in 1.7.

Laptop mode:
- no local photo understanding claim;
- no photo editing claim;
- no image generation claim;
- Jeff uses the documented friendly placeholder.

AI Max mode:
- fast local visual understanding;
- background visual-memory enrichment;
- local image editing through Bossman Studio;
- no participant computer-control or filesystem access.

## Model split

Do not assume one model must do everything.

Recommended architecture:
- **Qwen2.5-VL** or compatible local VLM = fast image understanding;
- **Qwen-Image-Edit** family = image editing;
- Bossman Studio remains the media execution plane.

The exact installed model IDs are runtime configuration. Do not hardcode a vendor marketing alias as a permanent contract.

## Telegram photo fast path

1. Telegram identity is authorized.
2. Largest acceptable JPEG/PNG/WebP is selected.
3. Size limit: 10 MiB.
4. Magic bytes are verified before model use.
5. Bytes are stored only under current participant:
   `<BOSSMAN_DATA_DIR>/pit-v1.7/personalities/<person_key>/media/inbox/`
6. Foreground Qwen vision receives only verified bytes + current question.
7. Foreground prompt uses a small token budget for speed.
8. Jeff sends the answer.
9. Only after that, a background job may run deeper visual analysis for memory.

No raw image base64 is written into conversation memory.

## Background visual memory

Background analysis is lower priority than live chat.

If foreground inference is busy, deep memory enrichment waits. If it remains busy beyond the bounded wait, enrichment is skipped instead of slowing the live conversation.

Background vision returns structured fields:
- scene;
- objects;
- visible_text;
- style;
- memory_hints;
- uncertain.

It is explicitly instructed not to infer:
- real-person identity;
- race/ethnicity;
- health/diagnosis;
- religion;
- political beliefs;
- sexual life;
- precise location/address;
- financial data.

Only neutral, short-lived visual-context candidates are collected initially. Default TTL = 7 days. Repeated useful visual preferences can later be promoted by the normal memory sorter.

## Photo editing

Jeff edits only the current participant's verified latest photo.

Participant:
`send photo → "убери фон" / edit intent`

Bossman:
`PhotoStore → Studio reference → Studio job(media=reference) → verified run → verified output bytes → Telegram`.

PIT does not directly call a diffusion process or shell.

The Studio model must be local and registered as available. For Qwen-Image-Edit, register a local Studio model/provider during AI-Max setup.

## Speed rules

Priority:
1. normal live chat;
2. explicit photo question / fast vision;
3. explicit requested photo edit;
4. background visual-memory analysis;
5. optional compaction/training work.

Background memory is never allowed to hold up a Telegram answer.

The fast vision prompt should stay short and use a small token cap. Deep memory analysis runs separately.

## Memory and privacy

The LLM never receives the PersonaVault path.
The VLM receives image bytes only for the current request.
The edit model receives only the verified participant-owned source image and edit prompt through Bossman Studio.

A/B users cannot reference one another's media paths.

Security/behavior/risk ledgers are never image-model context.

## Laptop behavior

Photo analysis:
`Фото получил. Разбирать и редактировать изображения локально я начну после переезда на AI Max 😊`

Image generation/editing:
`Скоро научусь, малышка 😊`

Do not fake media capability before AI Max live acceptance.

## Acceptance

PASS requires:
- JPEG/PNG/WebP magic-byte validation;
- oversize refusal before model;
- cross-user photo isolation;
- fast vision output;
- background memory does not delay foreground;
- sensitive visual inference is filtered;
- restart preserves verified own-photo reference;
- edit uses only current participant photo;
- Studio output hash/type verified;
- no direct shell/filesystem tool exposed to participant model;
- laptop path makes no local-photo claim;
- exact-SHA AI Max live vision/edit evidence.
