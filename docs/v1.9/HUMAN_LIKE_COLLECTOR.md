# Human-like data collector (rc19/i-collector)

Status: **feature branch only** — `rc19/i-collector`, not merged into the release
candidate, not activated, awaiting lead/owner GREEN LIGHT. Documentation here is
not activation or certification.

Workstream I builds a way for Bossman to gather information from the web the
way a careful, considerate human researcher does — so it keeps working
(including when Claude or any cloud model is unavailable) and can feed
Bossman's memory with sourced, verifiable facts.

## What it does

`bossman collect "<topic>" --sources <file> --max-pages N`

1. Reads an **owner-approved source list** (one `http(s)://` URL per line,
   `#`-comments allowed) — never a link the owner did not put there. No
   crawling, no "related pages", no search-engine discovery.
2. Visits pages **one at a time**, through Bossman's own existing Playwright
   browser runtime (`bcc.v2.browser_control.BrowserManager`) — the same
   engine the rest of the product uses for browser tools — so JS-heavy pages
   render exactly as a human would see them.
3. Checks **robots.txt** before every page (`bcc/collector/robots.py`,
   stdlib `urllib.robotparser`). A disallow is honoured; an unreachable or
   erroring robots.txt fails closed (treated as disallow); a plain 404/410
   ("nothing published") is treated as allowed, matching the standard
   convention.
4. Waits at least **5–10 seconds between pages on the same domain**
   (`--min-delay`, floored at 5s) and enforces a **daily page cap per
   domain** (`--daily-cap`), persisted across runs so restarting the process
   does not reset the cap.
5. Reads at a human pace: a short settle-in wait plus a couple of gentle
   scrolls before extracting text (best effort; never blocks the run if the
   page cannot be scrolled).
6. Extracts candidate facts **deterministically — no model call anywhere in
   this pipeline** — by scoring sentences against the topic's own words and
   a small library of unit patterns (bandwidth, power, cores, clock speed,
   capacity, process node) a human would recognise as "a specific fact".
   This is what keeps the collector working when Claude is unavailable.
7. Every fact carries full **provenance**: source URL, retrieved-at (UTC),
   the exact quoted sentence, a selector (`innerText#s<N>`), a SHA-256 hash
   of the page's extracted text, and the robots.txt verdict.
8. Writes a JSON fact bundle and a Markdown summary to
   `<data-dir>/runs/<run-id>/{facts.json,summary.md}`, plus a full
   append-only JSONL audit ledger (`ledger.jsonl`) of every page attempt.
9. Optionally (`--memory`) offers non-UNKNOWN facts to Bossman's memory
   through the **one existing path**, `bcc.v2.memory.facts.FactStore`, in
   `mode="append"` — never overwriting. Conflicting facts about the same
   (subject, predicate) from different sources stay side by side; nothing is
   silently merged. Requested `--attributes` that no approved source
   answered are recorded explicitly as `UNKNOWN`, never guessed.

STOP and PAUSE are honoured throughout: a `STOP` file inside the run's own
data dir ends the run immediately (partial results are still saved); the
owner-wide pause file for this rc19 owner-test cycle
(`C:\Users\asd\Bossman\rc19-owner-test.PAUSE` by default, overridable via
`BOSSMAN_RC19_PAUSE_FILE`) pauses the run — it resumes on its own once the
file is removed. The collector also lowers its own process priority
(best-effort, `psutil`) so it does not compete for CPU like a scraper would.

## What it refuses to do, by design

Not omissions — refused on purpose, because "human-like" here means acting
like a considerate human reader, not a bot trying to hide:

- fake user-agent rotation
- browser fingerprint spoofing
- proxy rotation / IP rotation to evade rate limits
- CAPTCHA solving or bypass (a CAPTCHA on a page is treated as "this site
  does not want an automated visitor" — the collector leaves, it does not
  solve it)
- logging in / defeating login walls
- paywall circumvention
- personal-data harvesting
- crawling links the owner did not put on the source list
- ignoring robots.txt disallow rules
- silently merging conflicting facts from different sources
- guessing a value when no approved source states it (kept as `UNKNOWN`)

The browser session's own User-Agent is never touched: it is Playwright's
genuine default Chromium identity. The one place the collector makes a raw
HTTP request of its own (fetching `robots.txt`, a machine contract rather
than a page a human reads) identifies with a single honest, unrotated
string reused from the product's existing `bcc.features.osiris.USER_AGENT`
— one product, one name.

## How to run it

```
bossman collect "AMD Ryzen AI Max+ 395 memory bandwidth" \
  --sources approved_sources.txt --max-pages 3 \
  --data-dir C:\Users\asd\Bossman\rc19-data\i-collector
```

`approved_sources.txt`:

```
# owner-approved, 2026-09-28
https://en.wikipedia.org/wiki/Ryzen
https://en.wikipedia.org/wiki/LPDDR5
```

`--headed` shows the browser window for debugging. `--json` prints a
machine-readable one-line summary. `--memory` also offers facts to an
isolated memory DB under `--data-dir` (see "Memory integration" below).

Equivalent: `python -m bcc.collector "<topic>" --sources <file> ...` from
inside `command-center` with `PYTHONPATH` set to the repo's `command-center`,
`bossman-core` and repo-root directories (Windows-style paths — a Git Bash
`/c/...` `PYTHONPATH` silently resolves against a different, older checkout
on this machine).

This is dispatched the same way the existing `bossman market` command is
(`bcc/terminal_cli/cli.py:cmd_market` → `cmd_collect`): a standalone,
in-process Bossman command with its own audit ledger and STOP file. It does
**not** require the Command Center backend to be running, because nothing in
the pipeline calls a model that would need routing through it — the
extraction is entirely deterministic. STOP/PAUSE are honoured regardless.

## Memory integration

`--memory` opens an isolated `Services`/SQLite DB under
`<data-dir>/memory/bcc.db` — the **same code** (`bcc.v2.memory.facts.FactStore`)
the rest of the product uses to write memory facts, just pointed at its own
database rather than the owner's live one. This demonstrates the real
integration without touching production data before the owner has reviewed
this branch. Wiring `--memory` to the *running* Command Center's own
`Services`/DB instead is a one-line change (swap the `Settings(data_dir=...)`
construction for the live instance's settings) that should happen only after
GREEN LIGHT, as a small, separately reviewed follow-up.

## Adding sources

The source list is a plain text file: one `http(s)://` URL per line,
`#`-comments and blank lines ignored. There is no other input format and no
automatic discovery — every URL the collector will ever visit has to be
written into this file by a human. Keep one file per topic/vetting session
so the owner can see exactly what was approved for what.

## Tests

`command-center/tests/test_collector_*.py` (45 tests, see the final workstream
report for the current pass count) plus `command-center/tests/collector_server.py`,
a small local, configurable HTTP server used by every test — no real site is
ever contacted. Robots.txt disallow/allow/404/5xx handling, per-domain delay
and daily cap, STOP/PAUSE mid-crawl, provenance-on-every-fact, conflicting
facts preserved, UNKNOWN attributes, offline/timeout/connection-refused
handling, and the honest single User-Agent are all covered by fast tests
using a stub browser session; one dedicated test drives the real
Playwright/Chromium browser against the local fake server to prove genuine
JS rendering and the real browser's own honest identity.
