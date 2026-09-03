from datetime import datetime, timezone, timedelta

import pytest

from osiris.grants import GrantBook, GrantError
from osiris.policy import ActionClass, PolicyDenied
from osiris.store import Store


def book(tmp_path):
    return GrantBook(Store(tmp_path))


def test_issue_and_authorize(tmp_path):
    b = book(tmp_path)
    g = b.issue(
        author="owner",
        source_or_subject="example.org",
        reason="grey robots, one domain",
        clause="scrape_unspecified_robots",
        ttl_hours=2,
    )
    assert g["status"] == "active"
    assert b.authorize(ActionClass.SCRAPE_UNSPECIFIED_ROBOTS, "example.org")["id"] == g["id"]


def test_wrong_subject_denied(tmp_path):
    b = book(tmp_path)
    b.issue(author="o", source_or_subject="a.example", reason="x", clause="export_outbound", ttl_hours=1)
    with pytest.raises(PolicyDenied):
        b.authorize(ActionClass.EXPORT_OUTBOUND, "b.example")


def test_level0_cannot_be_granted(tmp_path):
    b = book(tmp_path)
    with pytest.raises(PolicyDenied):
        b.issue(author="o", source_or_subject="x", reason="no", clause="leaked_dump", ttl_hours=1)


def test_expire_due(tmp_path):
    b = book(tmp_path)
    g = b.issue(author="o", source_or_subject="x", reason="t", clause="ttl_extension", ttl_hours=1)
    g["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    b.store.grant_put(g)
    closed = b.expire_due()
    assert closed and closed[0]["status"] == "expired"
    with pytest.raises(PolicyDenied):
        b.authorize(ActionClass.TTL_EXTENSION, "x")


def test_no_empty_grant(tmp_path):
    b = book(tmp_path)
    with pytest.raises(GrantError):
        b.issue(author="", source_or_subject="x", reason="r", clause="paid_api", ttl_hours=1)
