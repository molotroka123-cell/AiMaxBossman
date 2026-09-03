"""Organization graph from stored facts. No inferred private-life edges."""
from __future__ import annotations

from .provenance import validate_fact
from .store import Store

ORG_PREDICATES = frozenset({
    "legal_name",
    "reg_id",
    "status",
    "jurisdiction",
    "director",
    "founder",
    "official_url",
})


class OrgGraph:
    def __init__(self, store: Store):
        self.store = store

    def ingest_fact(self, fact: dict) -> int:
        fact = validate_fact(fact)
        fid = self.store.fact_insert(fact)
        subj = fact["subject"]
        self.store.node_upsert(subj, "org", str(fact.get("label") or subj), {})
        pred = fact["predicate"]
        obj = fact["object"]
        if pred in ("director", "founder") and isinstance(obj, str):
            self.store.node_upsert(obj, "person_office", obj, {"role": pred})
            self.store.edge_add(subj, pred, obj, fact["passport"])
        elif pred == "official_url" and isinstance(obj, str):
            self.store.node_upsert(obj, "url", obj, {})
            self.store.edge_add(subj, "official_url", obj, fact["passport"])
        return fid

    def snapshot(self) -> dict:
        return self.store.graph()
