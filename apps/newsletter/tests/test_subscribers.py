import pytest
from apps.newsletter.subscribers import ConsentError, Store

SECRET = "test-secret-not-for-production"


@pytest.fixture
def s(tmp_path):
    return Store(str(tmp_path / "subs.db"), SECRET)


def sub(s, email="reader@example.com", lang="ru", ints=("bitcoin",)):
    return s.subscribe(email, lang, list(ints), "test-form", "policy-2026-11")


def test_secret_is_required(tmp_path, monkeypatch):
    monkeypatch.delenv("NEWSLETTER_SECRET", raising=False)
    with pytest.raises(ConsentError):
        Store(str(tmp_path / "x.db"))


def test_pending_is_not_mailable_until_confirmed(s):
    r = sub(s)
    assert r["state"] == "pending" and s.segment() == []
    s.confirm(r["confirm_token"])
    assert [x["email"] for x in s.segment()] == ["reader@example.com"]


def test_confirm_needs_the_right_token(s):
    r = sub(s)
    with pytest.raises(ConsentError):
        s.confirm(r["id"] + ".0000")
    with pytest.raises(ConsentError):
        s.confirm(s.token(r["id"], "unsubscribe"))  # a token for another purpose does not confirm


def test_dedupe_by_normalised_email(s):
    a = sub(s, " Reader@Example.com ")
    b = sub(s, "reader@example.com")
    assert a["id"] == b["id"] and b["duplicate"] and s.counts()["pending"] == 1


def test_confirmed_duplicate_gets_no_new_token(s):
    r = sub(s)
    s.confirm(r["confirm_token"])
    again = sub(s)
    assert again["state"] == "confirmed" and again["confirm_token"] is None


def test_unsubscribe_removes_from_segment_and_suppresses(s):
    r = sub(s)
    s.confirm(r["confirm_token"])
    s.unsubscribe(s.segment()[0]["unsubscribe_token"])
    c = s.counts()
    assert s.segment() == [] and c["unsubscribed"] == 1 and c["suppressed"] == 1


def test_resubscribe_after_unsubscribe_needs_new_confirmation(s):
    r = sub(s)
    s.confirm(r["confirm_token"])
    s.unsubscribe(s.token(r["id"], "unsubscribe"))
    again = sub(s)
    assert again["state"] == "pending" and s.segment() == []
    s.confirm(again["confirm_token"])
    assert len(s.segment()) == 1 and s.counts()["suppressed"] == 0


def test_hard_bounce_and_complaint_are_permanent(s):
    r = sub(s, "a@example.com")
    s.confirm(r["confirm_token"])
    s.record_bounce("a@example.com", hard=True)
    r = sub(s, "b@example.com")
    s.confirm(r["confirm_token"])
    s.record_complaint("b@example.com")
    for email in ("a@example.com", "b@example.com"):
        with pytest.raises(ConsentError):
            sub(s, email)
    assert s.segment() == [] and s.counts()["suppressed"] == 2


def test_soft_bounce_keeps_the_subscriber(s):
    r = sub(s)
    s.confirm(r["confirm_token"])
    s.record_bounce("reader@example.com", hard=False)
    assert len(s.segment()) == 1


def test_segmentation_by_language_and_interest(s):
    rows = (("a@example.com", "ru", ["bitcoin"]), ("b@example.com", "en", ["defi", "bitcoin"]), ("c@example.com", "cz", ["regulation"]))
    for email, lang, ints in rows:
        s.confirm(sub(s, email, lang, ints)["confirm_token"])
    assert {x["email"] for x in s.segment(interest="bitcoin")} == {"a@example.com", "b@example.com"}
    assert [x["email"] for x in s.segment(language="cz")] == ["c@example.com"]
    assert s.segment(language="ru", interest="defi") == []


@pytest.mark.parametrize("bad", ["", "no-at-sign", "a@b", "two@@example.com", "sp ace@example.com"])
def test_invalid_email_refused(s, bad):
    with pytest.raises(ConsentError):
        sub(s, bad)


def test_unknown_language_interest_and_missing_consent_refused(s):
    with pytest.raises(ConsentError):
        s.subscribe("a@example.com", "de", ["bitcoin"], "form", "p1")
    with pytest.raises(ConsentError):
        s.subscribe("a@example.com", "en", ["nft"], "form", "p1")
    with pytest.raises(ConsentError):
        s.subscribe("a@example.com", "en", ["bitcoin"], "", "p1")


def test_consent_evidence_and_history_are_stored(s):
    r = sub(s)
    s.confirm(r["confirm_token"])
    s.record_delivery(r["id"], "sent", "mock-001")
    row = s.db.execute("SELECT * FROM subscribers WHERE id=?", (r["id"],)).fetchone()
    assert row["consent_source"] == "test-form" and row["policy_version"] == "policy-2026-11"
    assert row["confirm_ts"] >= row["consent_ts"]
    types = [e["type"] for e in s.db.execute("SELECT type FROM events WHERE subscriber_id=? ORDER BY id", (r["id"],))]
    assert types == ["state:pending", "state:confirmed", "sent"]


def test_backup_copies_everything(s, tmp_path):
    s.confirm(sub(s)["confirm_token"])
    assert s.backup(str(tmp_path / "backup.db")) == 1
