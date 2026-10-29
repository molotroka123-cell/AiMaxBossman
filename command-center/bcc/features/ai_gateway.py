"""AI Gateway feature router (/api/ai-gateway):
- GET  /ai-gateway/status          — gateway status, defaults, and active provider trust policy
- GET  /ai-gateway/policy          — trusted, experimental, and blocked provider lists
- POST /ai-gateway/policy/allow    — owner: explicitly allow a provider host as trusted or experimental
- POST /ai-gateway/policy/revoke   — owner: revoke a provider host back to unknown (blocked)
- POST /ai-gateway/inspect         — dry-run classification, local scrubbing, minimal context packaging, and decision
- GET  /ai-gateway/audit           — metadata-only local audit log (never logs full prompt/response text by default)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..security.ai_gateway import ProviderTrustTier, get_ai_gateway
from . import Feature

router = APIRouter(prefix="/ai-gateway", tags=["ai-gateway"])


class AllowProviderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = Field(min_length=1, max_length=255)
    tier: str = Field(default="trusted", max_length=32)


class RevokeProviderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = Field(min_length=1, max_length=255)


class InspectRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: dict[str, Any] = Field(default_factory=lambda: {"kind": "openai_compat", "base_url": "https://openrouter.ai/api/v1", "name": "openrouter"})
    model: dict[str, Any] = Field(default_factory=lambda: {"name": "qwen/qwen3.8-flash:free", "kind": "cloud"})
    messages: list[dict[str, Any]] = Field(default_factory=list)
    context_meta: dict[str, Any] = Field(default_factory=dict)
    has_local_fallback: bool = False


@router.get("/status")
async def gateway_status() -> dict[str, Any]:
    gw = get_ai_gateway()
    recent = gw.audit.recent(limit=20)
    return {
        "status": "active",
        "log_raw_text_default": gw.audit.log_raw_text,
        "defaults": {
            "confidential_domains_local_only": [
                "personal_memory",
                "personal_docs",
                "business",
                "finance",
                "keys",
                "private_repo",
            ],
            "cloud_provider_policy": "trusted_whitelist_only_after_local_scrub_and_minimal_context",
            "experimental_provider_policy": "public_or_synthetic_only",
            "unknown_provider_policy": "blocked_until_owner_approves",
            "max_history_messages": gw.minifier.max_history_messages,
            "max_context_chars": gw.minifier.max_context_chars,
        },
        "policy": gw.policy.to_dict(),
        "recent_audit_count": len(recent),
        "stats": gw.audit.stats(),
    }


@router.get("/policy")
async def get_policy() -> dict[str, Any]:
    return get_ai_gateway().policy.to_dict()


@router.post("/policy/allow")
async def allow_provider(body: AllowProviderIn) -> dict[str, Any]:
    gw = get_ai_gateway()
    tier_str = body.tier.strip().lower()
    if tier_str not in ("trusted", "experimental"):
        raise HTTPException(422, {"code": "invalid_tier", "message": "tier must be 'trusted' or 'experimental'"})
    gw.policy.allow_provider(body.host, tier=ProviderTrustTier(tier_str))
    return {"ok": True, "policy": gw.policy.to_dict()}


@router.post("/policy/revoke")
async def revoke_provider(body: RevokeProviderIn) -> dict[str, Any]:
    gw = get_ai_gateway()
    gw.policy.revoke_provider(body.host)
    return {"ok": True, "policy": gw.policy.to_dict()}


@router.post("/inspect")
async def inspect_request(body: InspectRequestIn) -> dict[str, Any]:
    gw = get_ai_gateway()
    insp = gw.classify_and_prepare(
        body.provider,
        body.model,
        body.messages,
        context_meta=body.context_meta,
        has_local_fallback=body.has_local_fallback,
    )
    return {
        "sensitivity": insp.sensitivity.value,
        "provider_tier": insp.provider_tier.value,
        "allowed": insp.allowed,
        "decision": insp.decision,
        "reason_code": insp.reason_code,
        "reason_message": insp.reason_message,
        "data_categories": insp.data_categories,
        "triggered_rules": insp.triggered_rules,
        "sanitized_messages": insp.sanitized_messages,
        "placeholders_assigned": list(insp.session.placeholder_to_original.keys()),
        "prompt_sha256": insp.prompt_sha256,
    }


@router.get("/audit")
async def gateway_audit(limit: int = 50) -> dict[str, Any]:
    gw = get_ai_gateway()
    return {
        "log_raw_text": gw.audit.log_raw_text,
        "items": gw.audit.recent(limit=max(1, min(limit, 500))),
    }


FEATURE = Feature(name="ai_gateway", router=router)
