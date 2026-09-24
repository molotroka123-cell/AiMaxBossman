"""Lightweight temporal personal operating graph for Bossman 1.5.

Graphiti-inspired ideas are implemented locally without a graph database:
provenance, valid-time history, supersession and point-in-time queries. The
graph stores references/metadata; secrets remain in Bossman's vault.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


ALLOWED_KINDS = {
    "project", "company", "person", "file", "task", "money", "model", "workflow",
    "market", "branch", "agent", "benchmark", "skill", "provider", "artifact",
}


@dataclass
class Node:
    id: str
    kind: str
    key: str
    label: str
    data: dict[str, Any] = field(default_factory=dict)
    source_ref: str = ""
    updated_at: float = 0.0


@dataclass
class Edge:
    id: str
    source: str
    relation: str
    target: str
    valid_from: float
    valid_to: float | None = None
    learned_at: float = 0.0
    invalidated_at: float | None = None
    source_ref: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def active_at(self, ts: float) -> bool:
        return self.valid_from <= ts and (self.valid_to is None or ts < self.valid_to)


class PersonalOperatingGraph:
    VERSION = 1

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.nodes: dict[str, Node] = {}
        self.edges: dict[str, Edge] = {}
        self._by_key: dict[tuple[str, str], str] = {}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if raw.get("version") != self.VERSION:
            return
        for row in raw.get("nodes") or []:
            try:
                node = Node(**row)
            except TypeError:
                continue
            self.nodes[node.id] = node
            self._by_key[(node.kind, node.key)] = node.id
        for row in raw.get("edges") or []:
            try:
                edge = Edge(**row)
            except TypeError:
                continue
            self.edges[edge.id] = edge

    def _save(self) -> None:
        payload = {
            "version": self.VERSION,
            "updated_at": time.time(),
            "nodes": [asdict(x) for x in sorted(self.nodes.values(), key=lambda n: n.id)],
            "edges": [asdict(x) for x in sorted(self.edges.values(), key=lambda e: e.id)],
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def upsert_node(self, kind: str, key: str, *, label: str = "",
                    data: dict[str, Any] | None = None, source_ref: str = "") -> str:
        kind, key = str(kind), str(key)
        if kind not in ALLOWED_KINDS:
            raise ValueError("unsupported graph node kind")
        if not key or len(key) > 300:
            raise ValueError("invalid graph node key")
        now = time.time()
        with self._lock:
            node_id = self._by_key.get((kind, key))
            if node_id is None:
                node_id = uuid.uuid4().hex[:16]
                self.nodes[node_id] = Node(node_id, kind, key, label or key,
                                           dict(data or {}), source_ref, now)
                self._by_key[(kind, key)] = node_id
            else:
                node = self.nodes[node_id]
                node.label = label or node.label
                if data:
                    node.data.update(data)
                if source_ref:
                    node.source_ref = source_ref
                node.updated_at = now
            self._save()
            return node_id

    def relate(self, source: str, relation: str, target: str, *, valid_from: float | None = None,
               source_ref: str = "", data: dict[str, Any] | None = None,
               supersede_relation: bool = False) -> str:
        if source not in self.nodes or target not in self.nodes:
            raise KeyError("edge endpoint missing")
        relation = str(relation).strip()
        if not relation or len(relation) > 120:
            raise ValueError("invalid relation")
        now = time.time()
        start = float(now if valid_from is None else valid_from)
        with self._lock:
            if supersede_relation:
                for edge in self.edges.values():
                    if edge.source == source and edge.relation == relation and edge.valid_to is None:
                        edge.valid_to = start
                        edge.invalidated_at = now
            edge_id = uuid.uuid4().hex[:16]
            self.edges[edge_id] = Edge(edge_id, source, relation, target, start, None,
                                       now, None, source_ref, dict(data or {}))
            self._save()
            return edge_id

    def invalidate(self, edge_id: str, *, at: float | None = None) -> None:
        with self._lock:
            edge = self.edges[edge_id]
            ts = float(time.time() if at is None else at)
            if edge.valid_to is None or ts < edge.valid_to:
                edge.valid_to = ts
            edge.invalidated_at = time.time()
            self._save()

    def neighbors(self, node_id: str, *, relation: str | None = None,
                  as_of: float | None = None, include_superseded: bool = False) -> list[dict]:
        if node_id not in self.nodes:
            raise KeyError(node_id)
        ts = time.time() if as_of is None else float(as_of)
        rows = []
        for edge in self.edges.values():
            if edge.source != node_id:
                continue
            if relation is not None and edge.relation != relation:
                continue
            if not include_superseded and not edge.active_at(ts):
                continue
            target = self.nodes.get(edge.target)
            rows.append({
                "edge": asdict(edge),
                "target": asdict(target) if target else None,
                "active": edge.active_at(ts),
            })
        rows.sort(key=lambda x: (x["edge"]["relation"], x["edge"]["valid_from"], x["edge"]["id"]))
        return rows

    def context(self, refs: list[tuple[str, str]], *, as_of: float | None = None,
                max_edges: int = 50) -> dict:
        nodes, edges = [], []
        for kind, key in refs:
            node_id = self._by_key.get((kind, key))
            if node_id is None:
                continue
            nodes.append(asdict(self.nodes[node_id]))
            edges.extend(self.neighbors(node_id, as_of=as_of)[:max_edges])
        return {"nodes": nodes, "edges": edges[:max_edges], "as_of": as_of}

    def record_task_result(self, *, task_id: str, role: str, branch: str | None,
                           benchmark_ref: str | None, skill_ref: str | None,
                           verified_success: bool, source_ref: str) -> None:
        task = self.upsert_node("task", task_id, data={"verified_success": bool(verified_success)},
                                source_ref=source_ref)
        agent = self.upsert_node("agent", role, source_ref=source_ref)
        self.relate(task, "handled_by", agent, source_ref=source_ref, supersede_relation=True)
        if branch:
            br = self.upsert_node("branch", branch, source_ref=source_ref)
            self.relate(task, "implemented_on", br, source_ref=source_ref, supersede_relation=True)
        if benchmark_ref:
            bm = self.upsert_node("benchmark", benchmark_ref, source_ref=source_ref)
            self.relate(task, "verified_by", bm, source_ref=source_ref)
        if skill_ref:
            sk = self.upsert_node("skill", skill_ref, source_ref=source_ref)
            self.relate(task, "used_skill", sk, source_ref=source_ref)
