# open-news integration — MIT attribution and boundaries

Upstream: https://github.com/alphap365/open-news
Pinned source: `ebb0e9b4deb0bf8fa8983e6324276a51f091ab43` (1.0.3).
Copyright (c) 2026 open-news. Full MIT terms are in `LICENSE`.

`UPSTREAM.json` records the exact upstream blobs and SHA-256 of the two
byte-identical Python modules redistributed by Bossman: `token_filter.py` and
`summarizer.py`. The wheel contains these files, this notice, the licence and
pin manifest. There is no floating pip/git install or added dependency.

Bossman adapts the upstream Google News RSS search endpoint/locale/query
construction into a single bounded async GET behind its existing per-call
approval queue. Offline exact URL dedupe uses the upstream tracking-parameter
set, without importing its URL resolver. The unmodified upstream dedupe may
resolve Google News URLs over the network; it is deliberately NOT imported.

This is a **reviewed subset**, not the entire upstream distribution. DDGS,
crawler, article downloads, arbitrary RSS URLs, redirect resolution, JavaScript,
TUI and stream/poll loops are not integrated. Search returns RSS snippets and
references, not full articles; no automatic publication, model call or trading.
An approval authorizes the exact search parameters, not the downloaded text.
`BOSSMAN_OFFLINE_MODE_ENABLED` blocks acquisition; processing supplied text
never needs a network or a model. Neither path reads personal files.

The extractive summarizer scores Latin words and falls back to leading
sentences for other scripts. It is not semantic Russian summarization or
verification of publishers' claims. Published dates are unverified source data,
not inferred event dates. Same-title reports from different outlets remain.

Source tests, mocked HTTP and package-asset tests are not live search, a live
model task, a Windows application ZIP test or full Bossman V8 Total acceptance.
