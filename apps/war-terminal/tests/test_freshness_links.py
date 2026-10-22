"""Owner 22.10: War Terminal listed tenders whose TED link answered 404 and kept expired notices as `open`."""
from datetime import date

from warterminal.connectors import ted
from warterminal.store import freshen


def _doc(**kw):
    base = {"id": "ted:650695-2026", "official_notice_id": "650695-2026", "source_id": "ted", "status": "open",
            "deadline": "2026-10-01T23:59:59Z", "source_url": "https://ted.europa.eu/en/notice/650695-2026"}
    base.update(kw)
    return base


def test_ted_notice_url_uses_resolving_form():
    assert ted.NOTICE_URL.format("650695-2026") == "https://ted.europa.eu/en/notice/-/detail/650695-2026"


def test_freshen_repairs_old_ted_url():
    assert freshen(_doc(), today=date(2026, 10, 22))["source_url"].endswith("/-/detail/650695-2026")


def test_freshen_closes_expired_open_notice():
    out = freshen(_doc(), today=date(2026, 10, 22))
    assert out["status"] == "closed" and out["status_note"]


def test_freshen_keeps_future_deadline_open_and_does_not_mutate_input():
    d = _doc(deadline="2026-12-01T23:59:59Z")
    out = freshen(d, today=date(2026, 10, 22))
    assert out["status"] == "open" and d["source_url"].endswith("/en/notice/650695-2026")


def test_freshen_leaves_other_sources_url_alone():
    d = _doc(source_id="prozorro", source_url="https://prozorro.gov.ua/tender/x", deadline=None)
    assert freshen(d)["source_url"] == "https://prozorro.gov.ua/tender/x"
