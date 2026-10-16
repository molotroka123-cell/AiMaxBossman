# AiMaxBossman Control Lab — owner prototype

Three standalone interactive panels in a single offline HTML page:

1. **Capability Tree** — navigable thematic tree. Categories are editorial groupings, **not** a verified import of all 922 registry leaves.
2. **Live Mission Control** — local demo controls for Ryzen AI Max+ 395 CPU, Radeon 8060S GPU, 128 GB unified memory, agent queue, tokens and costs.
3. **Roadmap Simulator** — sliders for monthly budget, agents, development pace and local workload, with pessimistic/base/optimistic projected milestones from 2026-10-16 to 2027-01-01.

## Run

Open `index.html` in a browser. No build tooling, network calls, external libraries or API credentials required. No real system actions.

## Data provenance

- **GitHub snapshot (checked 2026-10-16):** main at `ef20df69bd91f45481551b1e010bd61e93641fd6`; PR #108 merged on 2026-10-13; PR #112 open as of the check, describes seven-branch integration and a 922-leaf feature registry in its candidate branch. These are not a claim of successful owner-acceptance or a working production release.
- **Demo only:** all resource utilization, agent runs, token rates, expenditures, task completion, roadmap percentages and predicted dates.
- No current machine telemetry is available in this prototype. Actual telemetry must display `N/A` until authenticated owner-local integrations produce timestamped events.
- Budget forecast uses hypothetical expenses, not receipts or an actual token ledger.
- The timeline uses explicitly assumed stage durations; no scientifically calibrated throughput estimate is implied.

## Safety and acceptance

- Do **not** serve privileged control endpoints to the public internet.
- Read-only APIs first, localhost bind or owner-authenticated proxy, redact keys, tokens, paths and prompts.
- Distinguish DEMO / GITHUB_SNAPSHOT / OWNER_TELEMETRY in every response with source and timestamp.
- Gate command execution, merges, spend and model self-modification behind explicit owner approval.
- No automatic merging of PR #112; production integration requires exact-SHA CI, Windows owner E2E, inference, retention and safety-gate evidence.
- Keep existing command center unchanged until smoke tests and owner acceptance pass.

## Next step

See [IMPLEMENTATION_PROMPT_RU.md](./IMPLEMENTATION_PROMPT_RU.md) for a short implementation handoff. Open a browser to `prototypes/control-lab/index.html` to view the prototype.
