# V7 Risks

## Auditor Metadata

- **AUDITOR_MODEL**: Perplexity
- **AUDITOR_PROVIDER**: Perplexity AI
- **AUDITED_SHA**: bed5e8c9ad2846d9669ac3c0462cfd02c2cd6bd5
- **DATE**: 2026-09-07

---

## Architectural Risks

### 1. WSG Becomes a Bottleneck

**Risk**: All subsystems query WSG; if it's slow or unavailable, everything breaks.

**Mitigation**:
- WSG read replicas for high-throughput queries
- Local caches with explicit TTL
- Graceful degradation: subsystems fall back to local state if WSG is down

**Rollback**: Disable WSG integration, revert to existing state systems.

### 2. Strategy Search Adds Latency

**Risk**: Generating and scoring strategies takes time; owner waits longer for first action.

**Mitigation**:
- Limit strategy search to multi-step or high-stakes missions
- Cache strategy templates for common patterns
- Parallel strategy generation

**Rollback**: Revert to heuristic-only selection for latency-sensitive missions.

### 3. Unified Memory Scheduler Breaks Existing Skills

**Risk**: Skills assume their own memory policies; unified scheduler changes behavior.

**Mitigation**:
- Gradual rollout per subsystem
- Compatibility mode for existing skills
- Explicit skill manifest declaring memory requirements

**Rollback**: Revert to per-subsystem memory policies.

### 4. Reality Compiler 2.0 Introduces Bugs

**Risk**: New effect verification logic has bugs; actions appear verified but aren't.

**Mitigation**:
- Parallel run with RC v0.1 for validation
- Explicit pre/post state snapshots for audit
- Canary missions for regression testing

**Rollback**: Disable RC 2.0, use v0.1.

### 5. Goal-First UX Overwhelms Owner

**Risk**: Too much information (strategies, counterfactuals, cognitive load) increases cognitive load.

**Mitigation**:
- Progressive disclosure: show details on demand
- Default to simple view, advanced view opt-in
- Owner feedback loop for UX iteration

**Rollback**: Hide new UI components, keep existing dashboard.

---

## Implementation Risks

### 1. Scope Creep

**Risk**: V7 becomes "everything we ever wanted" instead of focused P0/P1.

**Mitigation**:
- Strict P0/P1/P2 prioritization
- V7.0 = P0 only; P1/P2 deferred
- Explicit "REJECTED" list to prevent backdoor features

### 2. Incompatible with V6 Systems

**Risk**: V7 changes break V6 Fleet, skills, or Computer Use.

**Mitigation**:
- Backward-compatible APIs
- Parallel run during migration
- Extensive canary testing

### 3. Owner Acceptance Failure

**Risk**: Owner rejects V7 changes; system reverts to V6.

**Mitigation**:
- Owner review at each P0 milestone
- Rollback plan documented and tested
- V7 opt-in, not forced migration

---

## Evidence Gaps

- **NOT_RUN**: WSG performance under load
- **NOT_RUN**: Strategy search success rates
- **NOT_RUN**: Unified memory scheduler impact on latency
- **EVIDENCE_GAP**: Owner cognitive load measurements
- **HYPOTHESIS**: Counterfactual simulation prevents mistakes (not yet tested)

---

*Independent audit by: Perplexity*
