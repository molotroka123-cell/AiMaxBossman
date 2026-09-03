import pytest

from osiris.collector import Collector
from osiris.events import EventBus
from osiris.policy import ActionClass, PolicyDenied
from osiris.sources import Fetcher, parse_robots, RobotsDecision
from osiris.store import Store


class MapTransport:
    def __init__(self, pages: dict[str, str]):
        self.pages = pages

    def get_text(self, url: str, headers: dict | None = None) -> str:
        if url not in self.pages:
            raise LookupError(url)
        return self.pages[url]


def test_robots_disallow_is_level0():
    body = "User-agent: *\nDisallow: /\n"
    assert parse_robots(body, "https://x.test/secret") == RobotsDecision.DISALLOW


def test_allow_robots_fetch(tmp_path):
    pages = {
        "https://ok.test/robots.txt": "User-agent: *\nAllow: /\n",
        "https://ok.test/org": "public org page",
    }
    st = Store(tmp_path)
    col = Collector(st, EventBus(), Fetcher(st, MapTransport(pages)))
    out = col.fetch_page("https://ok.test/org")
    assert out["robots"] == "allow"
    assert out["action"] == ActionClass.PUBLIC_PAGE_ALLOW_ROBOTS.value


def test_unspecified_needs_grant(tmp_path):
    pages = {"https://grey.test/robots.txt": "", "https://grey.test/p": "x"}
    st = Store(tmp_path)
    col = Collector(st, EventBus(), Fetcher(st, MapTransport(pages)))
    with pytest.raises(PolicyDenied):
        col.fetch_page("https://grey.test/p")
    col.grants.issue(
        author="owner",
        source_or_subject="grey.test",
        reason="grey robots one host",
        clause="scrape_unspecified_robots",
        ttl_hours=1,
    )
    out = col.fetch_page("https://grey.test/p")
    assert out["action"] == ActionClass.SCRAPE_UNSPECIFIED_ROBOTS.value


def test_l0_text_blocked(tmp_path):
    st = Store(tmp_path)
    col = Collector(st, EventBus(), Fetcher(st, MapTransport({})))
    with pytest.raises(PolicyDenied):
        col.record_org_fact(
            subject="p",
            predicate="legal_name",
            obj="x",
            source="combo list dump",
            url="https://example",
            method="http_get",
            license="none",
            confidence=1,
        )


def test_fact_and_graph(tmp_path):
    st = Store(tmp_path)
    col = Collector(st, EventBus(), Fetcher(st, MapTransport({})))
    col.record_org_fact(
        subject="org:1",
        predicate="director",
        obj="Ivanov I.I.",
        source="egrul",
        url="https://egrul.example/1",
        method="registry",
        license="public-registry",
        confidence=0.8,
    )
    g = col.graph.snapshot()
    assert any(n["id"] == "org:1" for n in g["nodes"])
    assert any(e["rel"] == "director" for e in g["edges"])


def test_export_needs_grant(tmp_path):
    st = Store(tmp_path)
    col = Collector(st, EventBus(), Fetcher(st, MapTransport({})))
    with pytest.raises(PolicyDenied):
        col.export_outbound("org:1")
