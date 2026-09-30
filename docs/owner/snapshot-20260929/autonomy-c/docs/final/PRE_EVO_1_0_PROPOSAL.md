# PRE-EVO 1.0 proposal

Status: proposal only. This document does not authorize stable Bossman to rewrite itself, relax safety, change budgets, install plugins, or promote models automatically.

## Entry condition

EVO work begins only after the owner-hardware acceptance of the frozen `release/bossman-owner` candidate. The current release remains the rollback anchor.

## First EVO loop

1. **Observe** — use the existing Flight Recorder and execution evidence to capture task, model/tool route, arguments, result, latency, cost, verification and owner intervention.
2. **Extract a skill candidate** — instruction + required tools + permissions + verifier + regression cases + version. Start with workflow/instruction improvements before weight training.
3. **Build a Golden Task set** — convert real successful and failed owner tasks into held-out regression cases. Training examples and evaluation cases must remain separated.
4. **Challenge the incumbent** — run the current skill/model and a candidate on the same held-out cases. Compare correctness, unnecessary tool calls, schema accuracy, recovery, cost, latency and owner interventions.
5. **Isolate changes** — any self-improvement candidate is built outside stable. It cannot edit release safety policy, approval rules, budgets or the rollback anchor.
6. **Owner decision** — Bossman may recommend promotion with evidence. Promotion remains explicit until a later EVO generation earns broader authority.
7. **Rollback** — every promoted skill/model/configuration keeps its prior version and a measured rollback trigger.

## Priority EVO capabilities

### Skill Registry / Skill Gym
A skill is a versioned product object, not a prompt fragment: instructions, tools, permissions, verifier, tests, provenance and compatibility. A skill is promoted only after held-out Golden Tasks pass.

### Teach by demonstration
Build on the existing Computer Apprentice. Record an owner demonstration, infer semantic targets and invariants, then test the candidate on changed data/layout before saving it as a skill. Coordinates alone never constitute a learned skill.

### Model Scout
Watch approved model sources, license/format metadata and hardware fit. A new model becomes a challenger, not an automatic replacement. Benchmark locally on the owner task set before recommendation.

### Unified-memory Resource Scheduler
Schedule resident LLM/image/video workers against measured memory pressure on the Ryzen AI Max+ owner machine. Unknown capacity stays unknown; no invented RAM/VRAM. Prefer queue/unload/reroute over machine thrashing.

### Plugin/MCP quarantine
`discover -> inspect -> sandbox -> permission diff -> negative tests -> owner approval -> install -> monitor -> revoke`. New plugins start with no ambient computer/files/network authority.

### Transaction journal and undo
For reversible mutations record before state, intended effect, after state and verifier. Support bounded rollback where the underlying action is actually reversible; never pretend an external irreversible effect was undone.

### Human takeover
Live task view with Stop, Pause and Take control. Returning control to Bossman requires a fresh observation so stale screen state cannot continue execution.

### Self-improvement inbox
Bossman may say: "candidate X solved 27/30 held-out cases vs 21/30, cost +8%, memory +4 GB; create/promote?" It must not say "I improved myself" without reproducible evidence.

## Explicit non-goals for PRE-EVO

- no silent self-edit of the stable branch;
- no autonomous live trading or financial authority increase;
- no automatic paid-cloud escalation;
- no safety/approval/budget downgrade to improve benchmark scores;
- no fabricated benchmark, model, image, video or provider success;
- no automatic installation of untrusted model files or remote code.

## First EVO success criterion

One real owner workflow is demonstrated, converted into a versioned skill candidate, evaluated on held-out variants, recommended with an evidence diff, explicitly approved, promoted, and rolled back successfully in a rehearsal. Until that loop is proven, EVO 1.0 remains recommendation-first.
