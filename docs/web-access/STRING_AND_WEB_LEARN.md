# String web access and `web_learn`

## Registration
- MCP server `string`, transport `http` (Streamable HTTP, official `mcp` SDK), endpoint template
  `https://mcp.usestring.ai/{secret:string_web_access_key}/v1/mcp` (key-in-path form from the vendor install doc).
- The key lives only in the encrypted vault (`settings_kv`, key `secret.string_web_access_key`). The MCP runtime
  substitutes it in memory at connect time. Set it with `PUT /api/web-access/key` (never echoed; mask is `...last4`).
- Code: `bcc/web_access/{policy,string_mcp,web_learn}.py`, `bcc/features/web_access.py`; minimal hooks in
  `bcc/v2/mcp_runtime.py` (http transport, redaction) and `bcc/features/tools_mcp.py` (generic `call` for `string` goes through policy).

## Routing and rules
Agents get `web.search` and `web.fetch`: String first, `browser.open` only when String is down / over quota / has no key.
Public http(s) only; no credentials in URL, no non-web ports, no localhost / private / tailnet / bare IPs, no
credential-like query params; secrets, e-mails, phones, local paths in queries refused; domain deny-list
(`BCC_WEB_DENY_DOMAINS` adds); 30/min and 500/day (`BCC_WEB_RATE_PER_MIN`, `BCC_WEB_RATE_PER_DAY`); response cap
20 000 chars (`BCC_WEB_MAX_CHARS`). Every response is fenced as untrusted data and cannot change instructions,
permissions, approvals or tool choice. One audit line per call: UTC time, tool, domain only.

## `web_learn` (first safe slice)
Topic from the owner goal list or a failed test, allow-list + refuse-list checked before any network call; one search,
at most 5 pages (one per domain); pages with injection markers are dropped; a short sourced note (URLs, retrieved-at,
license/terms flag, short excerpt) is written as a CANDIDATE to `<data>/web_learn/candidates/<id>/` (never inside the
repo, never merged). `promote_candidate` is fail-closed: needs a PASS from a verifier different from the author, and
even then only marks `verified_for_review`. Hook for the orchestra: task type `web_learn`, entry
`bcc.web_access.web_learn.run_web_learn(topic, origin, web=..., out_root=...)`; `POST /api/web-access/learn` triggers it by hand.
Not done: scheduling inside the autonomy package, wiring the independent verification gate (default is refuse).
