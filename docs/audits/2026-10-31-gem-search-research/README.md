# Gem Search as a research-only sidecar — receipt (31.10)

Source: h100envy/gem-search @ a04fae4 (MIT), fetched to D:\Bossman\gem-search\src, not vendored.
Runner: `tools/gem_search_research.py start --port 8851` (live run on the owner PC: exit 0,
`launch_enabled: false`, `mode: dry_run`). Bossman's own search proxy keeps :8850.

| Check | Result |
|---|---|
| Engine runs on Windows | PASS after the msvcrt lock shim (upstream imports Unix-only `fcntl`) |
| Bundled demo (16 fictional posts through the extension API) | PASS: 6 narrative groups, 5 shortlisted, 3 need data; 0 launches |
| Token launches | DISABLED by the project's own `data/launch-paused`; dry_run; no wallet / Pinata / paid X / Grok key |
| Refusal next to a wallet, `.env`, PINATA_JWT, LAUNCH_BRIDGE_URL, SPIDER_ALLOW_LAUNCH | PASS (tests/test_gem_search_research.py) |
| Real X feed via the Chrome extension | NOT_RUN — needs the extension installed in the owner's Chrome |
| Grok reviewers | NOT_RUN — no xAI key by design |

Not taken into Bossman: the memecoin autolaunch (Solana / pump.fun treasury, SOL budget) and the $GEMSEARCH token
tooling. The UI still shows an «Economy autolaunch» card with a Resume button; with no wallet and no Pinata it cannot
launch, and the runner re-creates the pause switch on every start.
