# Virtual bot hardening and GitHub hygiene

## Run

From the repository root:

```powershell
python -m pip install -r solana_volume_suite/requirements.txt
python solana_volume_suite/start_prototype.py --no-browser
```

Open http://127.0.0.1:8501 and paste the temporary **local mock** token printed by
the launcher. Alternatively put a freshly generated local token in the suite's
`.env` as DASHBOARD_API_TOKEN; replace the example placeholder before use.
The launcher reads only the listed local settings, not provider credentials.
Direct uvicorn invocation requires DASHBOARD_API_TOKEN already in the environment.
Use one worker on loopback. The supplied launchers enforce those defaults.

## Security behavior

- Every /api HTTP request requires a constant-time checked Bearer token. Missing,
  wrong or placeholder tokens are rejected. Startup fails if no valid token exists.
- Expensive start, mock-wallet generation and search operations permit five requests
  per rolling minute per shared client credential, across all IPs. Legacy start
  aliases share a quota. Other API calls allow 120/minute. Authenticated stop
  routes are exempt so throttling or unsafe environment changes cannot disable stop.
  Bodies over 8 KiB are rejected; wallet counts are bounded to 1–100.
- Authenticated WebSockets accept a Bearer header, or a token in the first text
  frame within three seconds. Tokens are never put in URLs. Four connections max;
  launcher frame limit is 8 KiB. Dashboard uses authenticated polling.
- Imports create no wallets, read no vaults, and start no tasks. Lifespan creates
  fictitious labels only. Existing vaults are never replaced by the dashboard.
- The offline loop assesses fixed hypothetical inputs. No AI/strategy calls,
  RPC requests, keypairs, signing, on-chain confirmations or transaction submission.
  Unsafe totals cannot become safe by slicing; missing provider data stays UNKNOWN.
- Explicit legacy encrypted-fixture generation uses a fresh
  `secrets.token_urlsafe(32)` password and publicly known mock key material.
  **Never fund these fixture addresses.** Loading non-mock key material fails closed.
- Jito submission and both Jupiter signing entry points fail before touching
  user keys or making network calls. Mainnet setup/launcher flags are blocked.
- SIGINT/SIGTERM handlers stop the standalone runner. Uvicorn owns dashboard
  signals; lifespan cancels/awaits the task, closes sockets and saves a bounded,
  secret-free state snapshot to runtime/state.json via atomic replacement.
  Abrupt OS termination cannot guarantee cleanup; on Windows SIGTERM from an
  external process may terminate immediately. Ctrl+C is the supported console stop.
- JSON audit events cover start, stop, rate limits, blocked execution and RPC
  timeout. Runtime state retains at most 300 events. Console audit records can
  be redirected by the operator; no secret values are included.
- Startup creates fresh simulation state; saved state is a review snapshot, not an
  automatic trading resume mechanism.

## Public repository discovery

```powershell
python solana_volume_suite/scripts/search_github.py --query fastapi --min-stars 100 --language Python --output solana_volume_suite/runtime/github_hygiene_results.csv
```

POST /api/github/search accepts query, min_stars, language. GET /api/github/results
returns the last successful search from process memory. Failures preserve that
cache and return an error, rather than replacing it with an empty success.

GitHub repository search supports **pushed:>DATE**, not updated:>DATE.
The upstream query uses pushed freshness; the local filter also checks updated_at
against 365 days. See [GitHub's repository search documentation](https://docs.github.com/en/search-github/searching-on-github/searching-for-repositories).
One request examines up to 100 candidates sorted by stars; it is not an exhaustive
search. No pagination loops, tokens, proxies, redirects, clones or downloads.
HTTP requests have a 10-second timeout. 403/429 preserves Retry-After and reset
cooldowns with no automatic retry, consistent with [GitHub rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api).

Results require public visibility, a license from a conservative open-source
allowlist, at least five stars, a description, and recent updates. Archived,
disabled, malformed, suspiciously named and duplicate results are excluded.
License metadata and popularity are discovery heuristics; no repository's code
has been audited. CSV text is escaped against formula injection. Browser output
uses textContent and canonical GitHub links, not untrusted HTML.

## Verification scope

The mission tests include authentication, alias quota bypass attempts, body/count
limits, credential import rejection, environment toggles, signals, state persistence,
no-network loop execution, stop/restart, vault preservation, RPC timeouts, mocked
GitHub filtering/CSV/errors/cooldown and legacy safety regressions.
Generate measured counts and a scoped secret scan with prepare_for_gemini.py.
The full monorepo, historical legacy strategies and dependency vulnerabilities
are not certified by this work.
