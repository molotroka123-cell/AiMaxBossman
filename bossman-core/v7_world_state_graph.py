"""V7 Adaptive Reality OS - World State Graph.

Authoritative state tracker with freshness, node provenance, and delta verification.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class StateNode:
    id: str
    entity_type: str
    attributes: Dict[str, Any]
    provenance_source: str
    last_verified_at: float = field(default_factory=time.time)
    confidence: float = 1.0
    version: int = 1

    def compute_hash(self) -> str:
        payload = {
            "id": self.id,
            "type": self.entity_type,
            "attr": self.attributes,
            "ver": self.version,
        }
        import json
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


class WorldStateGraph:
    """In-memory authoritative graph of observed reality."""

    def __init__(self):
        self._nodes: Dict[str, StateNode] = {}
        self._edges: List[Dict[str, Any]] = []

    def set_node(
        self,
        node_id: str,
        entity_type: str,
        attributes: Dict[str, Any],
        source: str,
        confidence: float = 1.0,
    ) -> StateNode:
        current = self._nodes.get(node_id)
        version = (current.version + 1) if current else 1
        node = StateNode(
            id=node_id,
            entity_type=entity_type,
            attributes=attributes,
            provenance_source=source,
            last_verified_at=time.time(),
            confidence=confidence,
            version=version,
        )
        self._nodes[node_id] = node
        return node

    def get_node(self, node_id: str) -> Optional[StateNode]:
        return self._nodes.get(node_id)

    def freshness_score(self, node_id: str, max_age_seconds: float = 60.0) -> float:
        node = self.get_node(node_id)
        if not node:
            return 0.0
        age = time.time() - node.last_verified_at
        if age <= 0:
            return 1.0
        if age >= max_age_seconds:
            return 0.0
        return (max_age_seconds - age) / max_age_seconds

    def snapshot(self) -> Dict[str, Any]:
        return {
            "timestamp": time.time(),
            "nodes": {k: v.__dict__.copy() for k, v in self._nodes.items()},
            "edge_count": len(self._edges),
        }
