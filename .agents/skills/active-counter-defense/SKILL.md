---
name: active-counter-defense
description: Active perimeter counter-defense for Bossman (deploys Canary honeypot traps, engages slow-drip scanner tarpits, compiles forensic attacker attribution dossiers from logs/Event ID 4625, and prepares reversible Windows Firewall IP quarantine rules gated on owner approval, without illegal external hack-back).
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
    attacker_ip:
      type: string
      description: Suspicious external IP address observed in failed logons or canary logs
    reason:
      type: string
      description: Observed intrusion vector (e.g. RDP brute-force 4625, canary hit, port scan)
  required: [attacker_ip]
output_schema:
  type: object
  properties:
    attacker_ip:
      type: string
    hits_count:
      type: integer
    block_command:
      type: string
    rollback_command:
      type: string
    requires_owner_yes:
      type: boolean
  required: [attacker_ip, block_command, rollback_command, requires_owner_yes]
---

# Active Perimeter Counter-Defense (`active-counter-defense`)

Use this skill when the owner asks for a counter-attack / active counter-response («встречная атака», «активный отпор», «поймать хакера в ловушку», «заблокировать атакующий IP») against an intrusion or brute-force attempt.

## Triggers

- встречная атака
- встречный отпор
- активная защита
- ловушка для хакера
- заблокировать ip атакующего
- tarpit
- канарейка

## Why Perimeter Deception & Tarpit Beats External Hack-Back

- Attackers almost always route through chains of compromised third-party servers (hospitals, public clouds, residential proxies) or spoofed IPs. Launching external offensive exploits or DDoS (`nmap`, `sqlmap`, `hydra`, etc.) is blocked by Line 1 (`HARD_DENY`) because it strikes innocent intermediate victims and exposes the owner's PC.
- Instead, Bossman executes **Active Perimeter Counter-Defense** on its own turf:
  1. **Canary Honeypot Traps (`/api/defense/counter/canary-trap`)**: Serves watermarked decoy payloads that record the intruder's exact IP, User-Agent, and timestamp while alerting the owner's Pult.
  2. **Scanner Tarpit (Смоляная яма)**: Holds the intruder's socket open with slow byte-drip responses (`X-Bossman-Counter-Defense: tarpit-engaged`), tying up their scanner threads at zero CPU cost to Bossman.
  3. **Forensic Attribution & Abuse Dossier (`defense.counter_dossier`)**: Compiles Event ID `4625` failed logons and canary hits into an evidence dossier ready for Abuse/CERT reporting.
  4. **Reversible Firewall Quarantine (`New-NetFirewallRule`)**: Generates a verified inbound block command and one-command rollback (`Remove-NetFirewallRule`), automatically refusing loopback (`127.0.0.1`, `::1`) and the owner's Tailscale subnet (`100.64.0.0/10`) so the owner can never be locked out.

## Safety & Permission Rules

- Never execute `New-NetFirewallRule` on the live PC without the owner's explicit «да».
- Never unblock or run offensive exploitation tools (`HARD_DENY` remains strictly enforced).
