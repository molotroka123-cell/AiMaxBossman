# Curated OpenClaw skills for Bossman

Source reviewed: `VoltAgent/awesome-openclaw-skills` at `37ad08c1b8e243d5f501c6fcaf7ac0b507bd83a1`.

The upstream project catalogs more than five thousand OpenClaw skills, but it
explicitly describes the collection as **curated, not audited**. Bossman therefore
does **not** vendor or auto-install any community skill from this list. The
repository stores only a pinned shortlist and admission requirements.

## Selection rule

A skill scores well when it improves an existing Bossman goal without becoming a
second authority model:

1. security/provenance before capability;
2. browser and research capabilities that can stay behind existing policies;
3. multi-agent patterns that reuse Fleet/Organization instead of replacing them;
4. deterministic validation and rollback;
5. no direct financial/trading, secret-export, bulk-outreach or unbounded host
   execution capability.

Every candidate still requires source review, a pinned provenance identity,
hostile/negative tests, owner approval and a rollback plan before promotion.

## Ranked shortlist

| Rank | Skill | Score | Use in Bossman | Mode |
|---:|---|---:|---|---|
| 1 | `azhua-skill-vetter` | 9.8 | Security-first vetting before a community skill reaches Skill Factory | candidate |
| 2 | `arc-trust-verifier` | 9.7 | Provenance/trust input for evidence-bound promotion | candidate |
| 3 | `playwright-mcp` | 9.6 | Browser control and Web Designer/browser acceptance | candidate |
| 4 | `agent-team-orchestration` | 9.4 | Role/handoff/review patterns for Fleet and Organization | reference |
| 5 | `agentgate` | 9.3 | HITL write-approval patterns; Bossman keeps canonical approval authority | reference |
| 6 | `arc-skill-gitops` | 9.2 | Versioning/rollback patterns for promoted skills | candidate |
| 7 | `config-validator` | 9.0 | Cheap configuration reliability for doctor/Gateway/local setup | candidate |
| 8 | `academic-deep-research` | 8.9 | Evidence-oriented research workflow | candidate |
| 9 | `arxiv-search-collector` | 8.7 | Reproducible paper collection for model/agent research | candidate |
| 10 | `agent-commons` | 8.5 | Independent reasoning challenge/review patterns | reference |
| 11 | `arc-security-audit` | 8.4 | Second-opinion skill-stack audit, never a release authority | reference |
| 12 | `alex-session-wrap-up` | 8.2 | Handoff/learning patterns; automatic push remains owner-gated | reference |

`candidate` means "worth source-vetting next", not "safe to install".
`reference` means the concept is useful but overlaps an existing Bossman
authority boundary and should normally be adapted rather than installed.

The exact registry URLs and per-skill gates live in
`integrations/openclaw/curated-skills.json`.

## Why some obvious choices are not promoted

- The upstream list includes security tools and sponsored ecosystem products.
  Sponsorship is not treated as a trust signal.
- Direct crypto/trading skills are intentionally excluded from this shortlist.
- Auto-merge, autonomous push and session-commit skills are not allowed to bypass
  Bossman's repository/owner controls.
- Browser automation stays high risk because a browser can cross from observation
  into real external effects.

## Admission sequence

```text
discover
  -> source review
  -> pin source/version/digest
  -> static + secret scan
  -> permission diff
  -> hostile tests in isolation
  -> shadow run
  -> measured comparison
  -> owner approval
  -> promote
  -> monitor
  -> rollback on regression
```

A community skill must never self-certify its own evidence or widen permissions
as part of promotion.

## Local validation

```bash
python tools/openclaw_curated.py --check
python tools/openclaw_curated.py
pytest -q tests/test_openclaw_curated.py
```

These commands only validate the metadata. They do not contact ClawHub and do not
install anything.

## Upstream provenance

The shortlist is derived from the MIT-licensed awesome list and pins the exact
upstream commit above. Individual community skills remain separate third-party
works with their own code, maintainers and security posture. Their source and
license must be reviewed independently before any code is copied or executed.
