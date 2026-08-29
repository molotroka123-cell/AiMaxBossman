# GitHub Repository Hygiene — AiMaxBossman

Purpose: keep the repository easy to audit by humans and AI agents months later.

## 1. Root must stay clean
Allowed at repository root:
- `README.md`
- `CHANGELOG.md` when introduced
- repo-wide config files (`.gitignore`, `.editorconfig`, etc.)
- top-level product directories such as `bossman-core/`, `command-center/`, `apps/`, `bossman-infra/`, `docs/`, `tools/`

Do NOT leave at root:
- old ZIP handoff packages
- screenshots
- temporary reports
- copied prompts named `final2`, `new`, `latest`, `fixed`
- generated logs/data

Move historical handoff ZIPs to `docs/archive/handoffs/YYYY/MM/` if they must remain tracked; otherwise attach them to a GitHub Release and remove them from the working tree.

## 2. Canonical documentation structure

```text
docs/
  architecture/
  specs/
  runbooks/
  audits/
    YYYY/
  decisions/
  archive/
    handoffs/
```

Use one canonical active document per topic. Old versions go to archive or Git history; do not keep `SPEC_v7_FINAL_REAL.md` beside the current spec.

## 3. File naming
Use lowercase kebab-case for normal docs and source-adjacent docs:

`computer-use-architecture.md`

Use date-first naming for audits and snapshots:

`2026-08-29__computer-use__implementation-audit__v1.md`

Never use:
- `final`
- `final-final`
- `new`
- `latest`
- `fixed2`
- spaces in new technical filenames

Version only when the document is an externally referenced specification. Otherwise rely on Git history.

## 4. Branch naming

```text
feature/computer-use-v1
fix/browser-download-timeout
refactor/toolkit-registry
chore/repo-hygiene
security/browser-approval-guard
```

Do not mix unrelated work in one branch.

## 5. Commit messages
Use Conventional Commits with an optional scope:

```text
feat(browser): add persistent Playwright computer-use tools
fix(browser): block sensitive clicks without approval
test(browser): cover risk classifier and default grants
docs(audit): add computer-use implementation audit
chore(repo): archive obsolete handoff artifacts
```

Commit subject rules:
- imperative mood;
- <= 72 chars when practical;
- one logical change per commit;
- no `update`, `changes`, `stuff`, `work`, or `fix things` as the entire subject.

For AI-generated commits, do NOT fake human authorship. If repository policy permits, add a trailer such as:

`Assisted-by: Claude Code`

Do not insert vendor attribution if the user/repository explicitly does not want it.

## 6. Pull request title
Same style as commits:

`feat(browser): add ComputerUse to all Bossman agents`

PR body must contain:
- Why
- What changed
- Security impact
- Tests
- Migration/install steps
- Known limitations
- Audit document path

## 7. Repository sorting procedure before every major audit
1. `git status --short` — working tree must be understood.
2. Identify root artifacts that belong in `docs/archive` or releases.
3. Detect duplicate specifications and stale audit files.
4. Ensure generated data, cookies, secrets, model files and browser profiles are ignored.
5. Ensure each app/core module has a small README or is discoverable from root README.
6. Run formatter/linter/tests already used by the repository; do not introduce a new formatter casually.
7. Generate a repo tree snapshot up to depth 3 in the audit.
8. Record branch and exact commit SHA.
9. Compare against the previous audit's unresolved findings.
10. Commit hygiene changes separately from product behavior whenever possible.

## 8. Files that must never be committed
- `.env` and secrets
- browser cookies/session databases
- Playwright auth state
- local Chromium user-data directories
- API keys/tokens
- downloaded personal files
- giant local model weights
- transient logs
- database dumps containing real user/customer data

## 9. AI audit friendliness
Every subsystem should have predictable anchors:
- spec: `docs/specs/<topic>.md`
- architecture: `docs/architecture/<topic>.md`
- runbook: `docs/runbooks/<topic>.md`
- audit: `docs/audits/YYYY/<date>__<topic>__<type>__vN.md`
- ADR: `docs/decisions/ADR-XXXX-<topic>.md`

This lets a future auditor search by subsystem name and immediately find intent, implementation notes, operations and historical findings.
