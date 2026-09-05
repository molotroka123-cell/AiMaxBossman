"""Privacy / Locality Router (§18).

PRIVATE и LOCAL_ONLY работа остаётся на trusted_local-узлах. Облако получает
только PUBLIC/INTERNAL работу и только минимизированный контекст. Если
локальной способности нет — CAPABILITY_UNAVAILABLE/BLOCKED, а не облачный
fallback. Секреты никуда, кроме trusted_local, не уходят.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import CLOUD, INTERNAL, LOCAL_ONLY, PRIVACY_LEVELS, PRIVATE, PUBLIC, TRUSTED_LOCAL, TRUSTED_REMOTE, NodeState

_RANK = {PRIVATE: 3, LOCAL_ONLY: 3, INTERNAL: 2, PUBLIC: 1}


@dataclass(frozen=True)
class PrivacyDecision:
    allowed: bool
    reason: str
    context_policy: str          # FULL | MINIMIZED | NONE


class PrivacyRouter:
    def __init__(self, *, allow_cloud_minimized_for_internal: bool = True) -> None:
        self.allow_cloud_minimized_for_internal = allow_cloud_minimized_for_internal

    def decide(self, *, requested_privacy: str, node: NodeState, contains_secrets: bool = False) -> PrivacyDecision:
        if requested_privacy not in PRIVACY_LEVELS:
            return PrivacyDecision(False, f"unknown_privacy:{requested_privacy}", "NONE")     # fail-closed
        if contains_secrets and node.trust_class != TRUSTED_LOCAL:
            return PrivacyDecision(False, "secrets_must_stay_on_trusted_local", "NONE")
        if requested_privacy in (PRIVATE, LOCAL_ONLY):
            if node.trust_class != TRUSTED_LOCAL:
                return PrivacyDecision(False, "private_task_requires_trusted_local_node", "NONE")
            if _RANK[node.privacy_level] < _RANK[requested_privacy]:
                return PrivacyDecision(False, "node_not_cleared_for_private_work", "NONE")
            return PrivacyDecision(True, "local_private", "FULL")
        if requested_privacy == INTERNAL:
            if node.trust_class in (TRUSTED_LOCAL, TRUSTED_REMOTE):
                return PrivacyDecision(True, "internal_on_trusted_node", "FULL")
            if node.trust_class == CLOUD and self.allow_cloud_minimized_for_internal:
                return PrivacyDecision(True, "cloud_with_minimized_context", "MINIMIZED")
            return PrivacyDecision(False, "internal_task_not_allowed_on_cloud", "NONE")
        return PrivacyDecision(True, "public_work", "FULL" if node.trust_class != CLOUD else "MINIMIZED")
