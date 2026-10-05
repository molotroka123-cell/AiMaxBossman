# Jeff 2.0 module: model_guard (order 20)

File: `command-center/bcc/pit/j2/model_guard.py`. Tests: `command-center/tests/test_jeff_2_model_guard.py`.

Found live: after a long uptime the local Ollama answered "Или Или Или" and CJK noise until it was restarted. This module
notices that within one turn, keeps such text away from the participant and tries the gentlest recovery.

## Detection

`assess(text, prompt=...)` returns `ok`, `suspect` or `garbage` plus problem codes:

| Code | Meaning |
|---|---|
| `empty` | nothing but whitespace |
| `repetition`, `repetition_loop` | one word repeated ("Или Или Или"), very low word diversity, an n-gram looping back to back |
| `foreign_script`, `foreign_script_trace` | CJK, Hangul, Arabic, Thai, Devanagari, Hebrew or full-width forms in the answer when the user did not write them; a lone stray character is only a suspect |
| `replacement_char`, `control_chars`, `template_leak` | mojibake, control bytes, leaked chat-template tokens |
| `symbol_noise`, `char_run` | mostly punctuation, or one character repeated 20+ times |
| `mixed_script_words` | Latin lookalikes inside Cyrillic words for a Russian prompt |
| `language_switch` | a long answer with no Russian to a Russian prompt (suspect only: it can be a translation) |

Normal answers are covered by a negative test list: code blocks, lists of numbers, URLs, short "Да.", French, repeated
"нет, нет, подожди".

## Canary and state

Three short prompts with a known answer rotate (capital of France, 2+3, colour of the sky); the answer must be clean and
contain one of the expected substrings. Results feed a state machine:

* `healthy` -> `suspect` on one weak sign;
* `degraded` at once on garbage, or on two weak signs within ten minutes;
* `recovering` after a recovery action; two good observations (canary or real turn) return it to `healthy`.

The optional background loop probes every 300 s when healthy and every 30 s otherwise (`start()` / `stop()`).

## Recovery

The default and only built-in action is **unload** (`keep_alive: 0` through the runtime's local adapter). The rules:

* at most one action per 120 s and three per hour; beyond that the module says `needs_owner` and notifies the owner once
  through `runtime.j2_escalate` when it exists;
* a restart action is used only when the owner injects it AND passes `allow_restart=True`, and only from the second
  attempt in an hour;
* the module never starts, stops or kills processes, and has no subprocess or signal code (a test greps the source);
* each action and each canary has a timeout, so a hung Ollama never stalls a turn.

## Turn behaviour

* `post_reply`: a garbage answer is replaced by a short apology in Jeff's voice and the recovery starts in the
  background; a suspect answer passes but counts. Answers marked `ctx.extra["served_by"] != "local"` are not judged.
* `augment`: while not healthy, one note asks the model for short simple sentences.
* `routing_hint()`: `{"avoid_local": bool, "state": ..., "needs_owner": ...}` for the router. It never forces a route.
* The module never blocks a turn.

## Status keys

`available`, `model`, `state`, `since`, `last_canary` (`id, ok, reason, latency_ms, at`), `canaries_run`,
`canary_failures`, `garbage_replies`, `recoveries` (`attempted, suppressed, last_action, last_at, last_result`),
`needs_owner`, `hint`.

## Privacy

Only problem codes, timings and counters are kept; no model text, prompt or participant text is stored.
`BOSSMAN_JEFF_J2_MODEL_GUARD=off` disables this module alone.
