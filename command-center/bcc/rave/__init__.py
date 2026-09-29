"""Agentic Rave (Bossman 1.9, workstream G): one prompt, several agents, each in
its own isolated git clone; per-agent and rave-wide pause / resume / STOP.

Design: docs/v1.9/AGENTIC_RAVE.md. API: bcc.features.rave. Terminal:
`bossman rave …` (bcc.rave.cli). This package adds no task engine, memory or
model registry of its own — it reuses Bossman's approvals, event bus,
ProcessTree, local sidecar and allowed roots.
"""
