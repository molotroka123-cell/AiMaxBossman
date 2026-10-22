"""Active Counter-Defense (Активный встречный отпор на собственном периметре):
Canary Honeypot Traps, Scanner Tarpit, Attacker Attribution Dossier, and Reversible Firewall Quarantine.

Safety boundary:
- Never launches offensive packets, exploits, or DDoS against external hosts (which is illegal and hits spoofed/compromised intermediaries).
- Operates strictly on Bossman's own perimeter: deceives and slows down intruding scanners (tarpit), trips instant canary alarms,
  collects attacker forensic attribution, and prepares ASK-gated reversible Windows Firewall quarantine rules (protecting loopback & Tailscale).
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..tools import REGISTRY, ToolContext, ToolResult, ToolSpec
from . import Feature

router = APIRouter(prefix="/defense/counter", tags=["defense-counter"])

TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")

DEFAULT_CANARY_PATHS: tuple[str, ...] = (
    "/.env",
    "/admin/.env",
    "/wp-admin",
    "/phpmyadmin",
    "/.git/config",
    "/api/internal/master-keys",
)


@dataclass
class IntrusionEvent:
    event_id: str
    ts: float
    kind: str  # "canary_hit" | "brute_force" | "scanner_tarpit"
    remote_ip: str
    path: str
    user_agent: str
    detail: str
    tarpit_applied_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "ts": self.ts,
            "iso_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.ts)),
            "kind": self.kind,
            "remote_ip": self.remote_ip,
            "path": self.path,
            "user_agent": self.user_agent[:200],
            "detail": self.detail[:300],
            "tarpit_applied_ms": self.tarpit_applied_ms,
        }


@dataclass
class CounterDefenseState:
    events: list[IntrusionEvent] = field(default_factory=list)
    hits_by_ip: dict[str, int] = field(default_factory=dict)

    def record(
        self,
        *,
        kind: str,
        remote_ip: str,
        path: str = "",
        user_agent: str = "",
        detail: str = "",
        tarpit_applied_ms: int = 0,
        now: float | None = None,
    ) -> IntrusionEvent:
        ts = time.time() if now is None else float(now)
        ip_clean = (remote_ip or "unknown").strip()
        self.hits_by_ip[ip_clean] = self.hits_by_ip.get(ip_clean, 0) + 1
        eid = hashlib.sha256(f"{ts}:{kind}:{ip_clean}:{path}:{len(self.events)}".encode("utf-8")).hexdigest()[:12]
        ev = IntrusionEvent(
            event_id=eid,
            ts=ts,
            kind=kind,
            remote_ip=ip_clean,
            path=path,
            user_agent=user_agent,
            detail=detail,
            tarpit_applied_ms=tarpit_applied_ms,
        )
        self.events.append(ev)
        if len(self.events) > 500:
            self.events = self.events[-500:]
        return ev


STATE = CounterDefenseState()


def validate_quarantine_target_ip(raw_ip: str) -> str:
    """Validate an attacker IP for local Windows Firewall quarantine.
    Refuses loopback, unspecified, multicast, link-local, and the owner's Tailscale CGNAT range (100.64.0.0/10)
    so the owner can never lock themselves out of Pult or Mobile.
    """
    try:
        ip = ipaddress.ip_address((raw_ip or "").strip().strip("[]"))
    except ValueError as exc:
        raise ValueError(f"некорректный IP-адрес: {raw_ip!r}") from exc
    if ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_link_local:
        raise ValueError(f"нельзя изолировать системный/loopback адрес: {ip}")
    if isinstance(ip, ipaddress.IPv4Address) and ip in TAILSCALE_CGNAT:
        raise ValueError(f"нельзя изолировать адрес Tailscale владельца ({ip}) — защита от самоблокировки")
    return str(ip)


def build_firewall_quarantine_plan(remote_ip: str, *, reason: str = "intrusion_canary") -> dict[str, Any]:
    """Build a reversible (block + rollback) Windows Defender Firewall command pair for an offending IP.
    Never executes automatically: requires explicit owner confirmation ('да').
    """
    ip_str = validate_quarantine_target_ip(remote_ip)
    safe_tag = ip_str.replace(":", "_").replace(".", "_")
    rule_name = f"Bossman-CounterDefense-Block-{safe_tag}"
    clean_reason = "".join(ch for ch in (reason or "intrusion") if ch.isalnum() or ch in ("_", "-", " "))[:60]
    block_cmd = (
        f'New-NetFirewallRule -DisplayName "{rule_name}" '
        f'-Direction Inbound -Action Block -RemoteAddress "{ip_str}" '
        f'-Description "Bossman Counter-Defense ({clean_reason})"'
    )
    rollback_cmd = f'Remove-NetFirewallRule -DisplayName "{rule_name}"'
    return {
        "target_ip": ip_str,
        "rule_name": rule_name,
        "requires_owner_yes": True,
        "block_command": block_cmd,
        "rollback_command": rollback_cmd,
        "reason": clean_reason,
    }


def generate_fake_canary_payload(token_id: str = "canary-trap-01") -> str:
    """Return a realistic-looking decoy config containing zero real credentials and a watermarked canary marker."""
    return (
        "# AUTO-GENERATED INTERNAL CONFIG (DO NOT DISTRIBUTE)\n"
        f"# CANARY_TRAP_ID={token_id}\n"
        "NODE_ENV=production\n"
        "HONEYPOT_STATUS=ACTIVE_COUNTER_DEFENSE\n"
    )


def build_attacker_dossier(remote_ip: str, state: CounterDefenseState = STATE) -> dict[str, Any]:
    """Compile all recorded hits, paths, user-agents, and a reversible firewall quarantine plan for an attacker IP."""
    ip_str = validate_quarantine_target_ip(remote_ip)
    matching = [e.as_dict() for e in state.events if e.remote_ip == ip_str]
    paths = sorted({e["path"] for e in matching if e.get("path")})
    uas = sorted({e["user_agent"] for e in matching if e.get("user_agent")})
    total_tarpit_ms = sum(int(e.get("tarpit_applied_ms") or 0) for e in matching)
    plan = build_firewall_quarantine_plan(ip_str, reason=f"hits={len(matching)}")
    abuse_report = (
        f"[INCIDENT REPORT / BOSSMAN ACTIVE COUNTER-DEFENSE]\n"
        f"Source IP: {ip_str}\n"
        f"Observed Events: {len(matching)}\n"
        f"Targeted Canary Paths: {', '.join(paths) or 'N/A'}\n"
        f"Observed User-Agents: {', '.join(uas) or 'N/A'}\n"
        f"Tarpit Delay Applied: {total_tarpit_ms} ms\n"
    )
    return {
        "attacker_ip": ip_str,
        "hits_count": len(matching),
        "paths_probed": paths,
        "user_agents": uas,
        "total_tarpit_ms": total_tarpit_ms,
        "events": matching[-20:],
        "quarantine_plan": plan,
        "abuse_report_text": abuse_report,
    }


async def tarpit_byte_stream(chunks: int = 4, delay_s: float = 0.25, token_id: str = "canary-trap-01"):
    """Slow-drip decoy stream that ties up an attacking scanner's worker thread without consuming local CPU."""
    payload = generate_fake_canary_payload(token_id).encode("utf-8")
    step = max(1, len(payload) // max(1, chunks))
    for i in range(0, len(payload), step):
        await asyncio.sleep(max(0.01, min(2.0, delay_s)))
        yield payload[i : i + step]


@router.get("/status")
async def get_counter_status() -> dict[str, Any]:
    return {
        "mode": "active_perimeter_counter_defense",
        "external_hackback": "PROHIBITED_BY_POLICY",
        "capabilities": ["canary_honeypot", "scanner_tarpit", "attacker_dossier", "reversible_firewall_quarantine"],
        "canary_paths": list(DEFAULT_CANARY_PATHS),
        "events_recorded": len(STATE.events),
        "distinct_ips": len(STATE.hits_by_ip),
        "recent_events": [e.as_dict() for e in STATE.events[-10:]],
    }


@router.get("/canary-trap")
async def trigger_canary_trap(request: Request, delay_ms: int = 400) -> StreamingResponse:
    """Honeypot endpoint: logs the intruder's metadata, applies a bounded tarpit delay, and streams decoy bytes."""
    client_ip = (request.client.host if request.client else None) or "127.0.0.1"
    ua = request.headers.get("user-agent", "")
    bounded_ms = max(40, min(4000, int(delay_ms)))
    ev = STATE.record(
        kind="canary_hit",
        remote_ip=client_ip,
        path=str(request.url.path),
        user_agent=ua,
        detail="Canary trap triggered; tarpit slow-drip engaged",
        tarpit_applied_ms=bounded_ms,
    )
    chunks = 4
    per_chunk_s = (bounded_ms / 1000.0) / chunks
    return StreamingResponse(
        tarpit_byte_stream(chunks=chunks, delay_s=per_chunk_s, token_id=ev.event_id),
        media_type="text/plain; charset=utf-8",
        headers={"X-Bossman-Counter-Defense": "tarpit-engaged"},
    )


@router.post("/dossier")
async def post_attacker_dossier(body: dict = Body(default_factory=dict)) -> dict[str, Any]:
    raw_ip = str(body.get("remote_ip") or "").strip()
    try:
        return build_attacker_dossier(raw_ip)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


async def _tool_counter_dossier(args: dict, ctx: ToolContext) -> ToolResult:
    raw_ip = str(args.get("remote_ip") or "").strip()
    dossier = build_attacker_dossier(raw_ip)
    return ToolResult(
        ok=True,
        output=dossier,
        preview=f"Counter-defense dossier for {dossier['attacker_ip']} (hits={dossier['hits_count']})",
    )


SPECS = [
    ToolSpec(
        name="defense.counter_dossier",
        description=(
            "Собрать досье на атакующий IP (срабатывания канареек, таймлайн, отчёт Abuse) "
            "и подготовить обратимую команду блокировки в Windows Firewall (требует подтверждения владельца «да»)."
        ),
        handler=_tool_counter_dossier,
        input_schema={
            "remote_ip": {"type": "string", "description": "Внешний IP-адрес атакующего"},
        },
        required=["remote_ip"],
        category="read",
        permission="terminal.read",
        source="active_counter_defense",
        default_effect="auto",
        external_output=True,
    ),
]


async def _setup(svc: Any) -> None:
    for _s in SPECS:
        REGISTRY.register(_s)


FEATURE = Feature(name="active_counter_defense", router=router, setup=_setup)
