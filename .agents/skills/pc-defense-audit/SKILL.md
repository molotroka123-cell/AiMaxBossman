---
name: pc-defense-audit
description: Read-only 2-line cyber-defense audit of the owner's Windows PC (Defender, Firewall, listening ports, unsigned autoruns, scheduled tasks from Temp, Security log 4625/4732/1102, UAC/SmartScreen, Tailscale funnel), diffing against the previous report and notifying Pult on new HIGH findings.
license: MIT
compatibility: BOSSMAN, OpenCode, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
  provenance: pc-defense-two-lines-20261022
required_tools: [terminal.run, memory.write]
permissions: [read, exec]
input_schema:
  type: object
  properties:
    json_out:
      type: string
      description: Output path for the JSON audit report
    baseline_json:
      type: string
      description: Previous audit report JSON for comparison
  required: []
output_schema:
  type: object
  properties:
    grade:
      type: string
      enum: [GREEN, YELLOW, RED]
    findings_count:
      type: integer
    new_high_count:
      type: integer
  required: [grade, findings_count]
---

# PC Cyber-Defense Audit (`pc-defense-audit`)

Use this skill whenever the owner or scheduler asks to check PC security, run a cyber-defense audit, verify Line 1 / Line 2 defenses, or inspect Windows Defender, firewall profiles, listening ports, scheduled tasks, or Tailscale exposure.

## Triggers

- проверь защиту
- аудит безопасности
- киберзащита пк
- pc defense audit
- проверь порты и планировщик

## Two Lines of Defense Playbook

1. **Line 1 — Never Let Bossman Become an Attack Tool**:
   - Terminal `HARD_DENY` and `TerminalPolicy.DANGEROUS` unconditionally block offensive network scanners, password crackers, and exploitation frameworks (`nmap`, `masscan`, `sqlmap`, `hydra`, `metasploit`, `mimikatz`, `impacket`, `nuclei`, etc.) even if approval is requested.
   - `_NEVER_AUTO_APPROVE_KINDS` blocks `outbound_send`, `network_send`, `cloud_send`, `cloud.ask`, `destructive`, `admin`, `self_improve`, and `skill_permissions` from auto-approval when `bypass_permissions=True`.
   - `plugin_security.validate_url` and `resolve_pinned_ip` block private/loopback IPv4/IPv6 literals, `.internal`/metadata hosts, and DNS rebinding.
2. **Line 2 — Never Let Keys or Owner Data Leak**:
   - Run `python tools/pc_defense_audit.py --json docs/north-star/evidence/pc_defense_audit_latest.json` (read-only).
   - Compare findings against the previous baseline report (`docs/north-star/evidence/pc_defense_audit_20261022.json`).
   - Record the audit summary via `memory.write` and alert the owner in Pult if any new `HIGH` finding appears.

## Safety & Permission Rules

- Strictly read-only observation: never read personal chats, photos, or secret values (Owner Rule 21.10).
- Never disable scheduled tasks, kill processes, or alter firewall rules without the owner's explicit "да".
