# Agent-skill provenance and notices

Discovery list: VoltAgent/awesome-agent-skills at `e4f7a502a78253550890e8b356d43f50192415ae`.
It is a catalog, not the author of the underlying skills. No wholesale import,
installer, hooks, executable helper, model weight or remote dependency is included.

| Upstream | Pinned commit | Adapted documents | License |
|---|---|---|---|
| obra/superpowers — Jesse Vincent | b36e0829c6d0140e93cfef2ca599b1b07d4a7797 | systematic-debugging; test-driven-development | MIT; copyright (c) 2025 Jesse Vincent |
| anthropics/skills — Anthropic | 41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f | webapp-testing; frontend-design; mcp-builder | Apache-2.0 |
| trailofbits/skills — Trail of Bits | d3323cefbcf645678b8dc481de204b02ad3d02dc | differential-review; property-based-testing; variant-analysis | CC-BY-SA-4.0 |

The eight SKILL.md files are modified, shortened Bossman-specific adaptations,
not unmodified redistributions or complete upstream installations. Every file
states its source, license and changes. MIT/Apache license text is included in
`licenses/`; the CC-BY-SA notice supplies the license URI and immutable original.
No endorsement by the upstream authors is claimed.

Removed or replaced upstream elements include broad tool grants, uninstalled
helpers/subagents, execute-before-inspection advice, environment-dump examples,
instructions to delete existing work and unconditional network-idle waits.
Bossman paths, owner control, actual-checkout evidence and existing stack take
precedence in these adaptations. This is not a claim that a prompt can enforce
runtime security: existing policy/approval/egress gates must still do so.

The lock file records the original commit and Git blob SHA plus a SHA-256 for
each local adaptation. These are integrity/provenance references, NOT digital
signatures or supply-chain trust attestations. A trusted reviewer must approve
any manifest update; replacing text and its hash together is not prevented by
a checksum. No updater or background fetch is shipped.
