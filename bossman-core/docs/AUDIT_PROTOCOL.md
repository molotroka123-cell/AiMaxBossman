# Bossman Audit Protocol and Naming Standard

## Why
Future audits must be comparable. A report called `audit-new-final.md` is useless to an AI agent six months later because its scope and baseline are unclear.

## Canonical audit filename

```text
YYYY-MM-DD__scope__audit-type__vN.md
```

Examples:

```text
2026-08-29__computer-use__implementation-audit__v1.md
2026-09-10__bossman-core__security-audit__v1.md
2026-10-01__full-repo__architecture-audit__v2.md
```

Allowed audit types:
- `implementation-audit`
- `security-audit`
- `architecture-audit`
- `performance-audit`
- `dependency-audit`
- `release-audit`
- `full-audit`

Store in:

`docs/audits/YYYY/`

## Mandatory audit header
Every audit begins with:

```yaml
---
audit_id: AUD-YYYYMMDD-SCOPE-NNN
scope: computer-use
type: implementation-audit
version: 1
status: pass | pass-with-findings | fail
repository: molotroka123-cell/AiMaxBossman
branch: <branch>
commit: <full SHA>
previous_audit: <path or null>
auditor: <human/model/tool>
created_at: <ISO-8601>
---
```

## Mandatory sections
1. Executive summary
2. Scope and exclusions
3. Baseline: branch + commit SHA
4. Architecture observed
5. Findings table
6. Security review
7. Tests and commands executed
8. Regression risks
9. Repository hygiene
10. Unresolved items
11. Recommended next actions
12. Files changed since previous audit

## Finding IDs
Use stable IDs:

`CU-SEC-001`, `CORE-ARCH-003`, `REPO-HYG-002`

Severity:
- P0 Critical
- P1 High
- P2 Medium
- P3 Low
- INFO

Each finding includes:
- ID
- severity
- status (`open`, `fixed`, `accepted`, `not-applicable`)
- evidence (file + line/function)
- impact
- fix
- verification

## Rules for future AI auditors
- Never claim a file was checked if it was not opened/searched.
- Distinguish observed facts from assumptions.
- Cite exact file paths and functions.
- Record commands actually executed, not commands you intended to run.
- Compare with previous unresolved finding IDs and carry them forward until closed.
- Do not renumber old finding IDs.
- Do not silently delete findings because code moved.
- If tests could not run, mark the audit `pass-with-findings` or `fail` as appropriate and state why.

## PR/commit relationship
Implementation PR should link its audit path. Audit document should record the final commit SHA (or be amended in a final docs-only commit with both SHAs documented).
