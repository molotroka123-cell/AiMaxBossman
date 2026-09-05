# Live web search

Command Center exposes a ChatGPT-style research flow through the web research
feature. It keeps the search query, result pages, extracted passages, citations,
and freshness checks in the local OSIRIS archive.

## Enable it

Set both feature flags and point the service at a SearXNG instance reachable
from the Command Center process:

```powershell
$env:BOSSMAN_WEB_RESEARCH_ENABLED = "1"
$env:BOSSMAN_OSIRIS_ENABLED = "1"
$env:BOSSMAN_WEB_SEARXNG_URL = "http://127.0.0.1:8080"
```

The SearXNG instance should have its `google` engine enabled. The application
accepts only SearXNG JSON search responses, extracts canonical result URLs,
and rejects search-result pages, tracking URLs, ad redirects, and untrusted
hosts before a page is opened. No Google credentials are stored in Bossman.

The endpoint is:

```http
POST /api/web/search
Content-Type: application/json

{"query":"latest GPU driver security advisory","limit":5,"fresh":true}
```

Each hit contains a stable reference token. Use that token with `web.open` or
`POST /api/web/refs` before reading a page, then use `web.cite` to verify a
passage. Search output is marked `external_untrusted` until it is cited and
the completion gate has verified the evidence.

## Local archive

The archive is written below the configured Command Center data directory as
OSIRIS web runs. It can be inspected with `GET /api/web/episodes`,
`GET /api/web/trail`, and `GET /api/web/citations/{obs_id}`. Retention and the
daily fetch budget are controlled by `BOSSMAN_WEB_*` settings; disabling the
feature prevents new network calls and does not erase existing evidence.

For an offline smoke test, point SearXNG at a local fixture or leave the
feature disabled. A disabled or unavailable backend returns an explicit
readiness/error code instead of silently fabricating results.
