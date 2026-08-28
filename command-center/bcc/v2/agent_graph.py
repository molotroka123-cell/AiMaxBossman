from __future__ import annotations

from dataclasses import asdict, dataclass

@dataclass(frozen=True, slots=True)
class AgentNode:
    id: str
    label: str
    status: str
    model: str = ""
    task: str = ""

@dataclass(frozen=True, slots=True)
class AgentEdge:
    source: str
    target: str
    kind: str = "delegates"

def graph_payload(nodes: list[AgentNode], edges: list[AgentEdge]) -> dict:
    ids = {n.id for n in nodes}
    safe_edges = [e for e in edges if e.source in ids and e.target in ids]
    # slots=True dataclass не имеет __dict__ — используем asdict
    return {
        "nodes": [asdict(n) for n in nodes],
        "edges": [asdict(e) for e in safe_edges],
    }
