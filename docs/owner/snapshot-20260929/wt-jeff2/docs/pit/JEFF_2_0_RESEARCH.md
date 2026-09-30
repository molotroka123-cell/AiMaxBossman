# Jeff 2.0 - Research Desk (module 6, order 60)

File: `command-center/bcc/pit/j2/research.py`, tests: `command-center/tests/test_jeff_2_research.py`.

## What it does

An explicit request («исследуй ...», «изучи», «проверь ...», «найди в интернете ...», «загугли», `research ...`,
`/research ...`) is answered with a report built ONLY from fetched sources. The module never calls a model, cloud
or local: the answer is extractive and every claim carries its source numbers.

1. `decompose` splits the question (several questions, "а также", comparisons «сравни A и B», `A vs B`) into at most
   4 sub-queries, deduplicated by a stemmed, order-insensitive key.
2. Search and fetch are injected callables (`create(runtime, search=..., fetch=...)`). Defaults are the existing
   keyless helpers: `runtime.models.web_results` (SearXNG or DuckDuckGo HTML) and `text_request` on the runtime's
   remote client (no redirects, 1 MiB cap, only HTTP 200).
3. Result URLs pass `safe_url` first: http(s) only, no credentials, no localhost, private, link-local or reserved
   literals, no `.local`/`.internal`-style names. At most 4 pages are opened, 6 s each, 20 000 characters kept.
4. Every page goes through `sanitize_fetched`: scripts, styles, hidden elements (`hidden`, `aria-hidden`,
   `display:none`, `font-size:0`), comments, control and bidi characters are dropped; sentences that look like
   instructions (RU/EN: "ignore previous instructions", role markers, chat-template tokens, "do not tell the
   user", exfiltration, shell commands, "act as") are removed and counted. Quoted sentences lose URLs and mentions.
5. `build_report` picks the sentences that best cover the query tokens, merges similar ones from several sources
   ("подтверждено несколькими источниками") and keeps sentences with differing numbers apart, flagging them.
6. Output lists numbered sources with title and URL and an uncertainty section: single source, snippet-only
   evidence, pages that did not open, conflicting numbers, stripped instruction-like fragments. With nothing
   usable the reply is an honest «Не удалось проверить» and it refuses to answer from memory.

## Timing, cache, limits

The hooks are short, so the work runs as one background task per participant. `pre_route` waits up to 0.3 s
(`asyncio.wait`, which never cancels the task; no `asyncio.shield`); otherwise the participant gets a "searching"
reply and asks «что нашёл» later. A second request while one is pending is refused politely. Rate limit: 6 requests
per 10 minutes per participant. The whole job is capped at 20 s.

The cache is participant-agnostic: reports are keyed by the normalised question (TTL 15 min, failures 60 s), pages
by URL (30 min); it holds public web data only and no person key. It is bounded to 128 entries per cache.

`augment` adds a data note (marked "not instructions, cite sources") when a cached report exists for exactly the
current question, and `post_reply` appends the source URLs if the model reply has no "Источники" block.

## Known gaps

The DNS name of a public-looking host is not resolved before fetching, so a hostile DNS record pointing at a private
address is not caught by `safe_url` (the default fetcher does not follow redirects and only reads HTTP 200
responses). Extraction is lexical: no synthesis, so answers are quotes rather than a fluent summary, by design.
