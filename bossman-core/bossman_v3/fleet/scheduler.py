"""Model-aware, explainable Fleet scheduler (§16, §17, §23).

Флот отвечает ГДЕ. Он не выбирает модель (это `bcc/v2/model_router` — Model
Broker) и не исполнителя (это marketplace организации). Узел может учитывать
«модель уже загружена» как один прозрачный компонент оценки, но никогда не
поверх приватности и способности.

Детерминированно: сначала жёсткие фильтры (каждый отказ — код причины), потом
лексикографический счёт. Никакого LLM.

Admission (§24, инновация 7): работа с явными требованиями к памяти
отклоняется до dispatch, если ни на одном пригодном узле нет запаса — дешевле
отказать сразу, чем убивать job после расхода ресурсов. Оценки не объявляются
точными.
"""
from __future__ import annotations

from typing import Iterable

from .models import NodeExplanation, NodeState, NodeStatus, PlacementRequirement
from .privacy import PrivacyRouter


class FleetScheduler:
    def __init__(self, privacy: PrivacyRouter | None = None, reliability=None) -> None:
        self.privacy = privacy or PrivacyRouter()
        # reliability(node_id, capability) -> float 0..1 — из статистики флота (наблюдаемые исходы)
        self.reliability = reliability or (lambda node_id, cap: 0.5)

    # --------------------------------------------------------- eligibility

    def reject_reasons(self, node: NodeState, req: PlacementRequirement) -> list[str]:
        r: list[str] = []
        if node.status != NodeStatus.ONLINE:
            r.append(f"node_{node.status.value}")
        pd = self.privacy.decide(requested_privacy=req.privacy, node=node, contains_secrets=req.contains_secrets)
        if not pd.allowed:
            r.append(pd.reason)
        missing = [c for c in req.capabilities if c not in node.capabilities]
        if missing:
            r.append("missing_capability:" + ",".join(sorted(missing)))
        if req.pools and not set(req.pools) & node.pools:
            r.append("pool_mismatch")
        if req.min_ram_gb and node.ram_free_gb < req.min_ram_gb:
            r.append(f"insufficient_memory:{node.ram_free_gb:.0f}<{req.min_ram_gb:.0f}GB")
        if req.min_gpu_memory_gb:
            free = node.gpu_free_gb if not node.unified_memory else max(node.gpu_free_gb, node.ram_free_gb)
            if free < req.min_gpu_memory_gb:
                r.append(f"insufficient_gpu_memory:{free:.0f}<{req.min_gpu_memory_gb:.0f}GB")
        if req.allowed_os and node.os_name not in req.allowed_os:
            r.append("os_not_allowed")
        lacking = [m for m in req.required_models if m not in node.models]
        if lacking:
            r.append("missing_model:" + ",".join(sorted(lacking)))
        if node.load > req.max_load:
            r.append(f"overloaded:{node.load:.2f}>{req.max_load:.2f}")
        if node.max_concurrency and node.active_work >= node.max_concurrency:
            r.append("at_max_concurrency")
        if req.anti_affinity_domains and node.failure_domain in req.anti_affinity_domains:
            r.append("anti_affinity_domain")
        return r

    # -------------------------------------------------------------- score

    def score(self, node: NodeState, req: PlacementRequirement) -> tuple[float, list[str]]:
        why: list[str] = []
        s = 0.0
        if node.trust_class == "trusted_local":
            s += 30; why.append("local_private" if req.privacy in ("private", "local_only") else "local_first")
        warm = set(req.required_models) & node.warm_models
        if warm:
            s += 25; why.append("model_warm")
        elif req.required_models:
            why.append("model_cold")
        if req.artifacts:
            local = set(req.artifacts) & node.artifacts
            if len(local) == len(req.artifacts):
                s += 20; why.append("artifacts_local")
            else:
                penalty = min(20.0, req.artifact_bytes / (1024 ** 3))       # ~1 очко за GB переноса
                s -= penalty; why.append(f"artifact_transfer_{len(req.artifacts) - len(local)}")
        headroom = node.ram_free_gb - req.min_ram_gb
        s += min(20.0, headroom / 8.0); why.append(f"{headroom:.0f}GB_memory_headroom")
        s += (1.0 - node.load) * 15; why.append("low_load" if node.load < 0.3 else "load_%.2f" % node.load)
        rel = float(self.reliability(node.node_id, req.capabilities[0] if req.capabilities else ""))
        s += rel * 10; why.append(f"reliability_{rel:.2f}")
        if req.prefer_node and node.node_id == req.prefer_node:
            s += 5; why.append("preferred_node")
        return s, why

    # ------------------------------------------------------------ explain

    def explain(self, nodes: Iterable[NodeState], req: PlacementRequirement) -> list[NodeExplanation]:
        out: list[NodeExplanation] = []
        for n in nodes:
            rejected = self.reject_reasons(n, req)
            if rejected:
                out.append(NodeExplanation(n.node_id, False, float("-inf"), tuple(rejected)))
            else:
                s, why = self.score(n, req)
                out.append(NodeExplanation(n.node_id, True, s, tuple(why)))
        # детерминированно: сначала пригодные по убыванию счёта, потом по id
        return sorted(out, key=lambda e: (not e.eligible, -(e.score if e.eligible else 0.0), e.node_id))

    def choose(self, nodes: Iterable[NodeState], req: PlacementRequirement) -> tuple[NodeExplanation | None, list[NodeExplanation]]:
        ex = self.explain(nodes, req)
        best = next((e for e in ex if e.eligible), None)
        return best, ex

    @staticmethod
    def admission_reason(explanations: list[NodeExplanation]) -> str:
        """Если ВСЕ узлы отклонены только по памяти/ресурсам — это admission
        reject (работа невозможна на текущем флоте), а не временная занятость."""
        if not explanations or any(e.eligible for e in explanations):
            return ""
        resource_only = all(all(r.startswith(("insufficient_", "at_max_concurrency", "overloaded")) for r in e.reasons)
                            for e in explanations)
        return "admission_rejected:no_node_has_resources" if resource_only else ""
