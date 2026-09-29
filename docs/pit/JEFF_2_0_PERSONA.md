# Jeff 2.0 module: persona (order 50)

File: `command-center/bcc/pit/j2/persona.py`. Tests: `command-center/tests/test_jeff_2_persona.py`.

Persona adapts how Jeff talks to each participant. It never widens permissions or facts and the owner overlay always wins.

## Inputs (all per participant)

1. Passport style layer (`bcc.pit.passport.read_style`, the Master Parser 2.0 narrative). Read only when the participant
   enabled memory. It is scanned with a fixed vocabulary (informal/formal, brief/detailed, humour, direct/soft, warm, emoji, with
   simple negation such as "не любит юмор") and turned into votes. The paragraph text is never copied into a prompt, so an
   inference cannot carry instructions.
2. Live mirror: an in-memory moving average of the participant's own manner (ты/вы, message length, humour markers, emoji). It
   needs two turns before it speaks. Persisted (numbers only, `<person_dir>/j2/persona.json`) only with memory consent; removed
   when memory is off.
3. Owner base scales (`settings.behavior_scales`) and the owner overlay (`bcc.pit.jeff_settings.style_for`).

## Traits and the owner overlay

Dimensions: brevity, depth, humor, directness, warmth, plus register and emoji. Each dimension moves at most +-2 from the owner's
base value. Any scale that the overlay sets (defaults or per participant) is locked: no delta and no persona phrase for it. The
overlay's own extra text is already in the system prompt and is not repeated. No personalisation happens when the participant's
personalisation switch is off.

## Prompt assembly within a budget

Parts are ranked (register, brevity, directness, humour, warmth, depth, emoji). The note must fit about 110 tokens (three
characters per token); the lowest-ranked parts are dropped first. `validate_note` refuses any note that mentions permissions,
access, commands, tools, passwords, keys, the owner, files, ignoring rules or inventing facts (two fixed disclaimers are allowed).
All phrase templates are validated by tests.

## Safe A/B

Three phrasings of the same content: `control`, `compact`, `explicit` (50/25/25). Assignment is a stable HMAC bucket of the
participant key and the experiment id, so a participant keeps one variant per experiment and different experiments reshuffle.
Every turn writes one line to `<data_dir>/pit-v1.7/j2/persona-ab.jsonl` (rotated at 1 MB): time, participant hash, message id,
experiment, variant, trait labels, note size, dropped parts; after the reply an `outcome` line with word counts only. No text.
`BOSSMAN_JEFF_J2_PERSONA_AB=off` forces control. Variants change wording, never content, permissions, facts or tools.

## Status keys

`turns`, `personalised`, `skipped_no_consent`, `variants`, `experiment`, `ab_enabled`, `locked_dimensions`,
`tracked_participants`, `log_events`.

`BOSSMAN_JEFF_J2_PERSONA=off` disables this module alone. The module only adds notes and never edits a reply.
