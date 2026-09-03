"""Collect public facts. Policy + grant + passport, in that order."""
from __future__ import annotations

from .events import EventBus
from .grants import GrantBook
from .graph import OrgGraph
from .policy import ActionClass, PolicyDenied, classify_text
from .provenance import make_passport, validate_fact
from .sources import Fetcher
from .store import Store


class Collector:
    def __init__(self, store: Store, bus: EventBus, fetcher: Fetcher):
        self.store = store
        self.bus = bus
        self.fetcher = fetcher
        self.grants = GrantBook(store)
        self.graph = OrgGraph(store)

    def _preflight(self, text: str) -> None:
        hit = classify_text(text)
        if hit is not None:
            raise PolicyDenied(hit, "request matches a sealed level-0 class")

    def fetch_page(self, url: str) -> dict:
        self._preflight(url)
        robots, action = self.fetcher.decide(url)
        grant = None
        if action is ActionClass.SCRAPE_UNSPECIFIED_ROBOTS:
            grant = self.grants.authorize(action, urlparse_host(url))
        else:
            self.grants.authorize(action, url)
        text, robots, action = self.fetcher.get_public(url)
        self.store.journal(
            "fetch.ok",
            url,
            {"robots": robots, "action": action.value, "grant_id": grant["id"] if grant else None},
        )
        self.bus.emit("fetch.ok", {"url": url, "robots": robots})
        return {"url": url, "robots": robots, "action": action.value, "chars": len(text), "text": text}

    def record_org_fact(
        self,
        *,
        subject: str,
        predicate: str,
        obj,
        source: str,
        url: str,
        method: str,
        license: str,
        confidence: float,
        grant_clause: str | None = None,
    ) -> dict:
        self._preflight(" ".join([subject, predicate, str(obj), source, url]))
        if grant_clause == ActionClass.HYPOTHESIS_TO_FACT.value:
            self.grants.authorize(ActionClass.HYPOTHESIS_TO_FACT, subject)
        if grant_clause == ActionClass.REGISTRY_RELATED_PERSONS.value:
            self.grants.authorize(ActionClass.REGISTRY_RELATED_PERSONS, subject)
        if grant_clause == ActionClass.PUBLIC_OFFICIAL_BY_OFFICE.value:
            self.grants.authorize(ActionClass.PUBLIC_OFFICIAL_BY_OFFICE, subject)
        fact = {
            "subject": subject,
            "predicate": predicate,
            "object": obj,
            "passport": make_passport(
                source=source, url=url, method=method, license=license, confidence=confidence
            ),
        }
        validate_fact(fact)
        fid = self.graph.ingest_fact(fact)
        self.store.journal("fact.stored", subject, {"id": fid, "predicate": predicate})
        self.bus.emit("fact.stored", {"id": fid, "subject": subject})
        return {"id": fid, **fact}

    def export_outbound(self, subject: str) -> dict:
        grant = self.grants.authorize(ActionClass.EXPORT_OUTBOUND, subject)
        facts = self.store.fact_list(subject)
        self.store.journal("export.outbound", subject, {"grant_id": grant["id"], "n": len(facts)})
        return {"subject": subject, "facts": facts, "grant_id": grant["id"]}


def urlparse_host(url: str) -> str:
    from urllib.parse import urlparse
    return (urlparse(url).hostname or url).lower()
