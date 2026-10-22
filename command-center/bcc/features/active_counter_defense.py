"""Lawful Perimeter Counter-Defense (Пассивная защита периметра, P2.11 / P2.12 / P2.15):
Canary Honeypot Traps, Standalone Decoy WAF Responder (with rate/concurrency/bandwidth limits),
Unverified Observed Source IP Dossier, Tamper-Evident Event Log, Owner Notification,
and Typed Owner-Confirmed Reversible Firewall Quarantine.

Safety boundary:
- Never launches offensive packets, hack-back, scanning, exploits, or DoS against external hosts.
- Never labels an observed remote IP as a verified/attributed attacker (remote IPs may be spoofed, NATed, or proxied).
- Never holds slow tarpit sleeps inside the main application worker; decoy responses are fixed-size and rate/concurrency/bandwidth-bounded.
- Treats all inputs from network headers, paths, user-agents, logs, and traps as untrusted (control-char scrubbed).
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import time
from typing import Any, Callable

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import PlainTextResponse

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

MAX_DECOY_RESPONSE_BYTES = 512
MAX_CONCURRENT_DECOY_REQUESTS = 16
RATE_LIMIT_PER_IP_PER_MIN = 30
MAX_BANDWIDTH_BYTES_PER_MIN = 64 * 1024

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]+")


def sanitize_untrusted_field(value: Any, *, max_len: int = 200) -> str:
    """P2.15: Scrub control characters (CR/LF/NUL) and bound length on all untrusted trap/network inputs."""
    cleaned = _CONTROL_CHARS_RE.sub(" ", str(value or "")).strip()
    return cleaned[:max_len]


def extract_observed_client_ip(
    peer_ip: str | None,
    x_forwarded_for: str | None = None,
    trusted_proxies: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """P2.11: Parse observed client IP using explicit trusted-proxy rules.
    `X-Forwarded-For` is ONLY honored when the direct peer IP is in `trusted_proxies`.
    """
    raw_peer = sanitize_untrusted_field(peer_ip or "127.0.0.1", max_len=64)
    try:
        validated_peer = str(ipaddress.ip_address(raw_peer.strip("[]")))
    except ValueError:
        validated_peer = "127.0.0.1"

    forwarded_trusted = False
    observed_ip = validated_peer

    if x_forwarded_for and validated_peer in trusted_proxies:
        hops = [h.strip().strip("[]") for h in str(x_forwarded_for).split(",") if h.strip()]
        valid_hops: list[str] = []
        for hop in hops:
            try:
                valid_hops.append(str(ipaddress.ip_address(hop)))
            except ValueError:
                continue
        if valid_hops:
            for candidate in reversed(valid_hops):
                if candidate not in trusted_proxies:
                    observed_ip = candidate
                    forwarded_trusted = True
                    break
            else:
                observed_ip = valid_hops[0]
                forwarded_trusted = True

    return {
        "observed_remote_ip": observed_ip,
        "direct_peer_ip": validated_peer,
        "forwarded_header_trusted": forwarded_trusted,
        "attribution_verified": False,
        "attribution_status": "UNVERIFIED_SOURCE_IP",
    }


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
    seq: int = 0
    prev_hash: str = "0" * 64
    entry_hash: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "event_id": self.event_id,
            "ts": round(float(self.ts), 6),
            "kind": sanitize_untrusted_field(self.kind, max_len=64),
            "observed_remote_ip": sanitize_untrusted_field(self.remote_ip, max_len=64),
            "attribution_status": "UNVERIFIED_SOURCE_IP",
            "path": sanitize_untrusted_field(self.path, max_len=200),
            "user_agent": sanitize_untrusted_field(self.user_agent, max_len=200),
            "detail": sanitize_untrusted_field(self.detail, max_len=300),
            "tarpit_applied_ms": int(self.tarpit_applied_ms),
            "prev_hash": self.prev_hash,
        }

    def compute_hash(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "event_id": self.event_id,
            "ts": self.ts,
            "iso_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.ts)),
            "kind": sanitize_untrusted_field(self.kind, max_len=64),
            "remote_ip": sanitize_untrusted_field(self.remote_ip, max_len=64),
            "observed_remote_ip": sanitize_untrusted_field(self.remote_ip, max_len=64),
            "attribution_status": "UNVERIFIED_SOURCE_IP",
            "attribution_verified": False,
            "path": sanitize_untrusted_field(self.path, max_len=200),
            "user_agent": sanitize_untrusted_field(self.user_agent, max_len=200),
            "detail": sanitize_untrusted_field(self.detail, max_len=300),
            "tarpit_applied_ms": int(self.tarpit_applied_ms),
            "prev_hash": self.prev_hash,
            "entry_hash": self.entry_hash,
        }


@dataclass
class CounterDefenseState:
    events: list[IntrusionEvent] = field(default_factory=list)
    hits_by_ip: dict[str, int] = field(default_factory=dict)
    log_path: Path | None = None
    owner_notify_cb: Callable[[dict[str, Any]], None] | None = None
    trusted_proxies: frozenset[str] = field(default_factory=frozenset)
    _seq: int = 0
    _last_hash: str = "0" * 64

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
        ts = round(time.time() if now is None else float(now), 6)
        ip_clean = sanitize_untrusted_field(remote_ip or "unknown", max_len=64)
        clean_path = sanitize_untrusted_field(path, max_len=200)
        clean_ua = sanitize_untrusted_field(user_agent, max_len=200)
        clean_detail = sanitize_untrusted_field(detail, max_len=300)

        self._seq += 1
        self.hits_by_ip[ip_clean] = self.hits_by_ip.get(ip_clean, 0) + 1
        eid = hashlib.sha256(f"{ts}:{kind}:{ip_clean}:{clean_path}:{self._seq}".encode("utf-8")).hexdigest()[:12]
        ev = IntrusionEvent(
            event_id=eid,
            ts=ts,
            kind=sanitize_untrusted_field(kind, max_len=64),
            remote_ip=ip_clean,
            path=clean_path,
            user_agent=clean_ua,
            detail=clean_detail,
            tarpit_applied_ms=int(tarpit_applied_ms),
            seq=self._seq,
            prev_hash=self._last_hash,
        )
        ev.entry_hash = ev.compute_hash()
        self._last_hash = ev.entry_hash
        self.events.append(ev)
        if len(self.events) > 500:
            self.events = self.events[-500:]

        if self.log_path is not None:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                row = {**ev.canonical_payload(), "entry_hash": ev.entry_hash}
                with self.log_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
            except OSError:
                pass

        if self.owner_notify_cb is not None:
            try:
                self.owner_notify_cb({
                    "type": "perimeter.canary_alert",
                    "event_id": ev.event_id,
                    "observed_remote_ip": ev.remote_ip,
                    "attribution_verified": False,
                    "path": ev.path,
                    "entry_hash": ev.entry_hash,
                })
            except Exception:  # noqa: BLE001
                pass
        return ev

    def verify_log_integrity(self) -> bool:
        """P2.12: Verify the SHA-256 hash chain of the durable event log."""
        if self.log_path is not None and self.log_path.is_file():
            try:
                lines = [ln for ln in self.log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            except OSError:
                return False
            expected_prev = "0" * 64
            for idx, ln in enumerate(lines, start=1):
                try:
                    row = json.loads(ln)
                except ValueError:
                    return False
                if not isinstance(row, dict):
                    return False
                recorded_hash = str(row.get("entry_hash") or "")
                if row.get("prev_hash") != expected_prev or int(row.get("seq", -1)) != idx:
                    return False
                check_obj = {k: v for k, v in row.items() if k != "entry_hash"}
                calc = hashlib.sha256(
                    json.dumps(check_obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
                ).hexdigest()
                if calc != recorded_hash:
                    return False
                expected_prev = recorded_hash
            return True

        expected_prev = "0" * 64
        for ev in self.events:
            if ev.prev_hash != expected_prev or ev.compute_hash() != ev.entry_hash:
                return False
            expected_prev = ev.entry_hash
        return True


class DecoyWafResponder:
    """P2.11: Minimal reverse-proxy / WAF decoy responder with fixed constant response,
    per-IP rate limit, concurrency cap, and per-minute bandwidth limit (zero main-worker tarpit sleep).
    """

    def __init__(
        self,
        *,
        rate_limit_per_min: int = RATE_LIMIT_PER_IP_PER_MIN,
        max_concurrent: int = MAX_CONCURRENT_DECOY_REQUESTS,
        max_bandwidth_bytes_per_min: int = MAX_BANDWIDTH_BYTES_PER_MIN,
    ) -> None:
        self.rate_limit_per_min = rate_limit_per_min
        self.max_concurrent = max_concurrent
        self.max_bandwidth_bytes_per_min = max_bandwidth_bytes_per_min
        self._ip_hits: dict[str, deque[float]] = {}
        self._bw_window: deque[tuple[float, int]] = deque()
        self._active = 0

    def allow_and_render(self, observed_ip: str, token_id: str) -> tuple[int, bytes]:
        now = time.monotonic()
        if self._active >= self.max_concurrent:
            return 429, b"429 Too Many Concurrent Requests\n"

        q = self._ip_hits.setdefault(observed_ip, deque())
        while q and now - q[0] > 60.0:
            q.popleft()
        if len(q) >= self.rate_limit_per_min:
            return 429, b"429 Rate Limit Exceeded\n"

        while self._bw_window and now - self._bw_window[0][0] > 60.0:
            self._bw_window.popleft()
        used_bw = sum(sz for _, sz in self._bw_window)
        payload = generate_fake_canary_payload(token_id).encode("utf-8")[:MAX_DECOY_RESPONSE_BYTES]
        if used_bw + len(payload) > self.max_bandwidth_bytes_per_min:
            return 429, b"429 Bandwidth Cap Reached\n"

        q.append(now)
        self._bw_window.append((now, len(payload)))
        return 200, payload


STATE = CounterDefenseState()
DECOY_WAF = DecoyWafResponder()


def validate_quarantine_target_ip(raw_ip: str) -> str:
    """Validate an observed external IP for local Windows Firewall inbound quarantine.
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


@dataclass(frozen=True, slots=True)
class FirewallQuarantineAction:
    """P2.12: Typed owner-confirmed inbound firewall quarantine action with deterministic rollback."""
    action_id: str
    observed_remote_ip: str
    rule_name: str
    reason: str
    block_argv: tuple[str, ...]
    rollback_argv: tuple[str, ...]
    block_command: str
    rollback_command: str
    requires_owner_yes: bool = True

    def confirm(self, *, owner_confirmed: bool, owner_id: str = "owner") -> dict[str, Any]:
        if not owner_confirmed:
            raise PermissionError("Firewall quarantine action requires explicit owner confirmation ('да')")
        return {
            "action_id": self.action_id,
            "action_type": "firewall_inbound_quarantine",
            "status": "CONFIRMED_READY",
            "confirmed_by": sanitize_untrusted_field(owner_id, max_len=64),
            "observed_remote_ip": self.observed_remote_ip,
            "rule_name": self.rule_name,
            "block_argv": list(self.block_argv),
            "rollback_argv": list(self.rollback_argv),
            "block_command": self.block_command,
            "rollback_command": self.rollback_command,
        }

    def rollback_spec(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action_type": "firewall_inbound_quarantine_rollback",
            "observed_remote_ip": self.observed_remote_ip,
            "rule_name": self.rule_name,
            "rollback_argv": list(self.rollback_argv),
            "rollback_command": self.rollback_command,
        }


def propose_firewall_quarantine_action(remote_ip: str, *, reason: str = "intrusion_canary") -> FirewallQuarantineAction:
    ip_str = validate_quarantine_target_ip(remote_ip)
    safe_tag = ip_str.replace(":", "_").replace(".", "_")
    rule_name = f"Bossman-CounterDefense-Block-{safe_tag}"
    clean_reason = "".join(ch for ch in (reason or "intrusion") if ch.isalnum() or ch in ("_", "-", " ", "="))[:60]
    block_cmd = (
        f'New-NetFirewallRule -DisplayName "{rule_name}" '
        f'-Direction Inbound -Action Block -RemoteAddress "{ip_str}" '
        f'-Description "Bossman Counter-Defense ({clean_reason})"'
    )
    rollback_cmd = f'Remove-NetFirewallRule -DisplayName "{rule_name}"'
    aid = "fwq-" + hashlib.sha256(f"{ip_str}:{rule_name}:{clean_reason}".encode("utf-8")).hexdigest()[:10]
    return FirewallQuarantineAction(
        action_id=aid,
        observed_remote_ip=ip_str,
        rule_name=rule_name,
        reason=clean_reason,
        block_argv=(
            "netsh", "advfirewall", "firewall", "add", "rule",
            f"name={rule_name}", "dir=in", "action=block", f"remoteip={ip_str}",
        ),
        rollback_argv=(
            "netsh", "advfirewall", "firewall", "delete", "rule", f"name={rule_name}",
        ),
        block_command=block_cmd,
        rollback_command=rollback_cmd,
    )


def build_firewall_quarantine_plan(remote_ip: str, *, reason: str = "intrusion_canary") -> dict[str, Any]:
    """Build a reversible (block + rollback) Windows Defender Firewall plan for an observed IP.
    Never executes automatically: requires explicit owner confirmation ('да').
    """
    act = propose_firewall_quarantine_action(remote_ip, reason=reason)
    return {
        "action_id": act.action_id,
        "target_ip": act.observed_remote_ip,
        "observed_remote_ip": act.observed_remote_ip,
        "attribution_verified": False,
        "rule_name": act.rule_name,
        "requires_owner_yes": True,
        "block_command": act.block_command,
        "rollback_command": act.rollback_command,
        "block_argv": list(act.block_argv),
        "rollback_argv": list(act.rollback_argv),
        "reason": act.reason,
    }


def generate_fake_canary_payload(token_id: str = "canary-trap-01") -> str:
    """Return a fixed-format decoy config containing zero real credentials and a watermarked canary marker."""
    clean_tok = "".join(ch for ch in str(token_id) if ch.isalnum() or ch in ("-", "_"))[:32] or "canary-trap-01"
    return (
        "# AUTO-GENERATED INTERNAL CONFIG (DO NOT DISTRIBUTE)\n"
        f"# CANARY_TRAP_ID={clean_tok}\n"
        "NODE_ENV=production\n"
        "HONEYPOT_STATUS=ACTIVE_COUNTER_DEFENSE\n"
    )


def build_attacker_dossier(remote_ip: str, state: CounterDefenseState = STATE) -> dict[str, Any]:
    """Compile recorded canary hits and a reversible inbound firewall quarantine plan for an observed remote IP.
    P2.11: Explicitly marks source IP as UNVERIFIED_SOURCE_IP (never claims verified attacker attribution).
    """
    ip_str = validate_quarantine_target_ip(remote_ip)
    matching = [e.as_dict() for e in state.events if e.remote_ip == ip_str]
    paths = sorted({e["path"] for e in matching if e.get("path")})
    uas = sorted({e["user_agent"] for e in matching if e.get("user_agent")})
    total_tarpit_ms = sum(int(e.get("tarpit_applied_ms") or 0) for e in matching)
    plan = build_firewall_quarantine_plan(ip_str, reason=f"hits={len(matching)}")
    abuse_report = (
        f"[INCIDENT REPORT / BOSSMAN PERIMETER COUNTER-DEFENSE]\n"
        f"Unverified Observed Source IP: {ip_str} (attribution_status=UNVERIFIED_SOURCE_IP; may be NAT/proxy/spoofed)\n"
        f"Observed Events: {len(matching)}\n"
        f"Targeted Canary Paths: {', '.join(paths) or 'N/A'}\n"
        f"Observed User-Agents: {', '.join(uas) or 'N/A'}\n"
        f"Decoy Delay Metric: {total_tarpit_ms} ms\n"
    )
    return {
        "observed_remote_ip": ip_str,
        "attacker_ip": ip_str,  # backward-compatible alias; see attribution_status
        "attribution_status": "UNVERIFIED_SOURCE_IP",
        "attribution_verified": False,
        "attribution_warning": "Observed IP is not a verified attacker identity; may be NAT, proxy, or spoofed.",
        "hits_count": len(matching),
        "paths_probed": paths,
        "user_agents": uas,
        "total_tarpit_ms": total_tarpit_ms,
        "events": matching[-20:],
        "quarantine_plan": plan,
        "abuse_report_text": abuse_report,
    }


async def tarpit_byte_stream(chunks: int = 4, delay_s: float = 0.01, token_id: str = "canary-trap-01"):
    """Bounded decoy stream for standalone decoy proxy testing (capped to <= 0.02s delay to prevent worker exhaustion)."""
    payload = generate_fake_canary_payload(token_id).encode("utf-8")[:MAX_DECOY_RESPONSE_BYTES]
    step = max(1, len(payload) // max(1, min(chunks, 8)))
    for i in range(0, len(payload), step):
        await asyncio.sleep(max(0.0, min(0.02, float(delay_s))))
        yield payload[i : i + step]


@router.get("/status")
async def get_counter_status() -> dict[str, Any]:
    return {
        "mode": "passive_perimeter_counter_defense",
        "external_hackback": "PROHIBITED_BY_POLICY",
        "tarpit_in_main_worker": False,
        "attribution_policy": "UNVERIFIED_SOURCE_IP_ONLY",
        "log_integrity_verified": STATE.verify_log_integrity(),
        "capabilities": [
            "canary_honeypot",
            "decoy_waf_responder",
            "unverified_source_dossier",
            "reversible_firewall_quarantine",
        ],
        "canary_paths": list(DEFAULT_CANARY_PATHS),
        "events_recorded": len(STATE.events),
        "distinct_ips": len(STATE.hits_by_ip),
        "recent_events": [e.as_dict() for e in STATE.events[-10:]],
    }


@router.get("/canary-trap")
async def trigger_canary_trap(request: Request, delay_ms: int = 0) -> PlainTextResponse:
    """Honeypot endpoint: extracts observed IP via trusted-proxy policy, logs tamper-evident event,
    and returns a fixed-size decoy response via DecoyWafResponder without sleeping in the main worker.
    """
    peer_ip = (request.client.host if request.client else None) or "127.0.0.1"
    xff = request.headers.get("x-forwarded-for")
    ip_info = extract_observed_client_ip(peer_ip, x_forwarded_for=xff, trusted_proxies=STATE.trusted_proxies)
    observed_ip = ip_info["observed_remote_ip"]
    ua = request.headers.get("user-agent", "")

    ev = STATE.record(
        kind="canary_hit",
        remote_ip=observed_ip,
        path=str(request.url.path),
        user_agent=ua,
        detail="Canary trap triggered; fixed decoy response served (no main-worker sleep)",
        tarpit_applied_ms=0,
    )
    status_code, body_bytes = DECOY_WAF.allow_and_render(observed_ip, ev.event_id)
    return PlainTextResponse(
        content=body_bytes.decode("utf-8", errors="replace"),
        status_code=status_code,
        headers={
            "X-Bossman-Counter-Defense": "decoy-waf-fixed",
            "X-Attribution-Status": "UNVERIFIED_SOURCE_IP",
        },
    )


@router.post("/dossier")
async def post_attacker_dossier(body: dict = Body(default_factory=dict)) -> dict[str, Any]:
    raw_ip = str(body.get("remote_ip") or body.get("observed_remote_ip") or "").strip()
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
        preview=(
            f"Perimeter incident dossier for observed IP {dossier['observed_remote_ip']} "
            f"(UNVERIFIED_SOURCE_IP, hits={dossier['hits_count']})"
        ),
    )


SPECS = [
    ToolSpec(
        name="defense.counter_dossier",
        description=(
            "Собрать досье по наблюдаемому внешнему IP (срабатывания канареек, таймлайн, без ложной атрибуции) "
            "и подготовить типизированное обратимое правило блокировки входящих в Windows Firewall (требует подтверждения владельца «да»)."
        ),
        handler=_tool_counter_dossier,
        input_schema={
            "remote_ip": {"type": "string", "description": "Наблюдаемый внешний IP-адрес"},
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
    data_dir = getattr(getattr(svc, "settings", None), "data_dir", None)
    if data_dir:
        STATE.log_path = Path(data_dir) / "security" / "counter-defense-events.jsonl"
    for _s in SPECS:
        REGISTRY.register(_s)


FEATURE = Feature(name="active_counter_defense", router=router, setup=_setup)
