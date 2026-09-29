# Jeff 2.0 - Memory Palace (module 4, order 40)

File: `command-center/bcc/pit/j2/memory_palace.py`, tests: `command-center/tests/test_jeff_2_memory_palace.py`.

## What it does

Retrieval over ONE participant's passport (`facts`, `events`, `style`), read through `passport.facts_layer`,
`raw/events.jsonl` and `style.json`. There is no other data source and no shared index: the engine takes a
`person_key` on every call and caches per person.

* Lexical: Okapi BM25 implemented in the module (k1 1.5, b 0.75, Lucene-style idf). Russian normalisation:
  lower-case, `ё` to `е`, stop words, negations removed from tokens, two-pass suffix stripping (stemming-lite).
* Semantic (optional): an injected `embed(list[str]) -> vectors` callable (sync or async). Relevance becomes
  `0.6 * bm25 + 0.4 * cosine`. Timeout 0.25 s; any failure falls back to lexical. Vectors are cached per person
  and dropped when the text is no longer stored. The module never loads a model itself.
* Strength: `score = relevance * (0.5 + 0.3 * retention + 0.2 * importance)`.
  `retention = exp(-age_days / stability)`, `stability = base * (1 + 3 * importance) * (2 if confirmed)`,
  base 45 days for facts and 14 for events. Importance comes from confidence, evidence kind (confirmed >
  explicit > inferred), category (identity-like weigh more) and number of corrections.
* Contradictions: same single-valued key with different values, or the same statement with opposite polarity.
  They are surfaced (prompt note "ask, do not pick" and a warning in the participant view), never merged.

## Participant answers (pre_route)

* "что ты обо мне знаешь / помнишь", "what do you know about me": a list with provenance per item (source
  kind and date from the passport envelope, confidence, freshness, confirmed, corrections) and the commands
  `/correct`, `/forget`, `/pause_memory`. A topic in the question filters the list.
* "почему ты так думаешь / откуда ты знаешь / how do you know": finds the supporting record (by the clause, or the
  items just shown) and states the basis; style inferences are labelled a guess; no record means an honest
  "unconfirmed".
* Slash commands are never intercepted.

## Consent and obedience

Consent is re-read from the vault on every call and the index is keyed by the file signature, so nothing is
served from a stale copy: `/correct` shows the new value on the next turn, `/forget` removes it from retrieval,
views and vector cache, `/pause_memory` and `/revoke_consent` return nothing at all. `events` need
`raw_history_enabled`; sensitive facts reach prompts only with `sensitive_memory_enabled` (the participant's own
view still lists them, marked local-only); personalization off keeps the view but adds no prompt notes; a remote
route (`ctx.extra["route_remote"]`) needs `remote_personalization_enabled`.

## Bounds

top-k 6, at most 12 listed, prompt notes 900 characters, view 1800 characters, 1000 facts and 300 events indexed,
32 cached people, 1500 cached vectors per person.

## Known gaps

`route_remote` is not yet set by the chat route; until it is, notes may reach a remote route (the pipeline notes
are still bounded and the existing `participant_context` gate is unchanged). Events are only as complete as
`append_raw_event` writers make them. Stemming-lite does not handle consonant alternation (`люблю` / `любит`),
which the optional embeddings cover.
