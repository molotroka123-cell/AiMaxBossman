# Fleet OS — 10 innovations beyond the base foundation

These are **directions, not ceilings**. Fable/Claude/GLM may improve, merge,
redesign or replace them when current repository evidence supports a better design.

Format: **Proposal → Foundation → Explanation → Implementation direction**.

## 1. Lease Renewal + Fencing Tokens
**Proposal:** Add renewable leases with monotonically increasing fencing tokens.
**Foundation:** LeaseManager + durable FleetStore.
**Explanation:** Expired/stale workers must not regain authority merely because they still run.
A fencing token gives executors a version they must present when mutating shared state.
**Implementation direction:** integrate with actual executor/receipt contracts and reject stale tokens.

## 2. Failure Domains + Placement Anti-Affinity
**Proposal:** Model machine/rack/network/cloud failure domains and avoid placing redundant roles together.
**Foundation:** Node labels + scheduler.
**Explanation:** Reviewer and executor on the same failing node provide little resilience.
**Implementation direction:** add topology/failure-domain scoring without requiring Kubernetes.

## 3. Warm Model Residency Scheduler
**Proposal:** Prefer nodes where required local models are already loaded when safe.
**Foundation:** ModelRuntime + resource scheduler.
**Explanation:** Large local models can take time and memory to load; reuse warm residency to reduce latency.
**Implementation direction:** make warmness one transparent score component, never override privacy/capability gates.

## 4. Artifact-Aware Placement
**Proposal:** Include artifact/data locality in placement.
**Foundation:** ArtifactDescriptor + Topology.
**Explanation:** Moving a 40 GB model or dataset can cost more time than executing on a slightly slower node.
**Implementation direction:** score transfer cost and verify content hashes end-to-end.

## 5. Dead-Letter + Quarantine Lane
**Proposal:** Failed tasks with exhausted retries move to a durable quarantine instead of looping forever.
**Foundation:** RetryPolicy + DeadLetterStore.
**Explanation:** Keeps fleet healthy and gives Organization/Executive OS a truthful blocker.
**Implementation direction:** preserve error evidence and require explicit recovery/requeue conditions.

## 6. Explainable Placement Decisions
**Proposal:** Every placement or rejection returns deterministic reasons and scores.
**Foundation:** Scheduler + placement_explain.
**Explanation:** Essential for debugging local-first routing and avoiding mysterious cloud escalation.
**Implementation direction:** expose through CEO/Fleet control plane and metrics.

## 7. Resource Treasury + Predictive Admission
**Proposal:** Estimate whether a task fits time/GPU/network/cloud budgets before dispatch.
**Foundation:** ResourceBudget/Usage + Executive Governor.
**Explanation:** Admission control is cheaper than killing a runaway job after it spends resources.
**Implementation direction:** use measured historical estimates with confidence ranges, not fake precision.

## 8. Trusted Event Journal
**Proposal:** Maintain a deduplicated durable event stream for Fleet lifecycle events.
**Foundation:** EventJournal + FleetEvent.
**Explanation:** Enables restart-safe observability, replay for state reconstruction, and audit without storing private reasoning.
**Implementation direction:** append operational facts only; do not treat events as side-effect verification.

## 9. Secure Node Identity + Attestation Boundary
**Proposal:** Treat node identity/authentication as a first-class trust boundary.
**Foundation:** NodeTransport protocol + CredentialBroker.
**Explanation:** A random machine must not self-register and receive sensitive work.
**Implementation direction:** use the repository's actual auth/PKI mechanism; mutual authentication and encrypted transport are mandatory for remote nodes.

## 10. Graceful Drain / Evacuation
**Proposal:** Support draining a node for reboot/update without losing work.
**Foundation:** NodeStatus.DRAINING + durable checkpoints + resume planner.
**Explanation:** Stop new placements, finish or checkpoint safe work, then move resumable tasks elsewhere.
**Implementation direction:** never migrate a side effect that cannot be proven safe/idempotent.

## Autonomy clause
Integration agents may discover additional Fleet improvements and implement them without asking for
ordinary engineering approval. They must preserve V2/V3 verification truth, permissions, idempotency,
security and project isolation. Do not expand into Autonomous Operations until Fleet itself is coherent.
