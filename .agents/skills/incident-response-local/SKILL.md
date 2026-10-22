---
name: incident-response-local
description: Local cyber-incident triage and rollback-ready remediation planner for Bossman (collects read-only process, port, and scheduled-task evidence on HIGH/MEDIUM findings and prepares a reversible one-command backup+fix script awaiting explicit owner confirmation).
license: MIT
compatibility: BOSSMAN, OpenCode, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
  provenance: pc-defense-two-lines-20261022
required_tools: [terminal.run, memory.search, memory.write]
permissions: [read, exec]
input_schema:
  type: object
  properties:
    finding_check:
      type: string
      description: Audit category that triggered triage (defender, firewall, ports, autorun, tasks, seclog, tailscale)
    finding_detail:
      type: string
      description: Summary of the suspicious finding
  required: [finding_check]
output_schema:
  type: object
  properties:
    triage_status:
      type: string
      enum: [EVIDENCE_COLLECTED, AWAITING_OWNER_YES, RESOLVED]
    rollback_ready:
      type: boolean
    proposed_command:
      type: string
  required: [triage_status, rollback_ready]
---

# Local Incident Response & Reversible Remediation (`incident-response-local`)

Use this skill when `pc-defense-audit` reports a `HIGH` or suspicious `MEDIUM` finding, or when the owner reports suspicious PC behavior, unexpected tasks/ports, or potential compromise.

## Triggers

- подозрительно
- взлом
- инцидент
- лишние задачи планировщика
- кто слушает порт
- incident response

## Deterministic Triage Workflow

1. **Collect Read-Only Forensics (`terminal.run`)**:
   - For `ports`: identify the owning PID, executable path, and Authenticode signature (`Get-NetTCPConnection`, `Get-Process`, `Get-AuthenticodeSignature`).
   - For `tasks`: inspect `TaskPath`, `TaskName`, `Author`, `Date`, `Execute`/`Arguments`, and verify whether the target script in `C:\Temp` exists and when it was last modified.
   - For `autorun`: check registry Run/RunOnce entries and binary hashes (`Get-FileHash -Algorithm SHA256`).
2. **Prepare Reversible Remediation + Rollback (`memory.write`)**:
   - Always generate a backup step first (e.g. `Export-ScheduledTask` to `%LOCALAPPDATA%\Bossman\backups\scheduled-tasks-<date>\`) before any `Disable-ScheduledTask` or firewall change, plus a one-command restore script.
3. **Gate on Explicit Owner Approval**:
   - Present the exact finding, backup location, action command, and rollback command to the owner. Execute nothing mutating until the owner explicitly replies «да».

## Safety & Permission Rules

- Never execute mutating remediation (disabling tasks, killing processes, deleting files, changing firewall rules) without explicit «да» from the owner.
- Never use offensive tools or counter-attack external hosts; strictly defend the local PC.
