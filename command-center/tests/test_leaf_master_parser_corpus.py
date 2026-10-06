"""authored_by_lane jeffa: real behavior of the append-only sealed Master Parser corpus."""
from __future__ import annotations

import base64

import pytest

from bcc.pit.master_parser.corpus import Corpus, normalize
from bcc.pit.master_parser.sources import RawMessage


def seal(s):
    return "SEALED:" + base64.b64encode(s.encode()).decode()      # reversible, not plaintext


def unseal(s):
    return base64.b64decode(s.split(":", 1)[1]).decode()


def msg(ref, text, *, source="tg/inbox", person="p1", ts=1000.0, role="participant", pid=""):
    return RawMessage(person_key=person, surface="telegram", source=source, source_ref=ref,
                      role=role, ts=ts, text=text, platform_message_id=pid)


@pytest.fixture()
def corpus():
    c = Corpus.in_memory(seal, unseal)
    yield c
    c.close()


def test_normalize_collapses_whitespace_and_case():
    assert normalize("  Привет   МИР \n") == "привет мир"
    assert normalize(None) == ""


def test_add_batch_counts_new_dup_and_known_and_advances_cursors(corpus):
    assert corpus.add_batch([msg("a:1", "Я люблю джаз")], {"tg": "1"}) == (1, 0)
    # same text from another source inside the dedupe window: provenance only, no new row
    assert corpus.add_batch([msg("b:1", "я  люблю ДЖАЗ", source="web", ts=1100.0, pid="77")], {"web": "5"}) == (0, 1)
    # the exact same (source, ref) again is already known: ignored
    assert corpus.add_batch([msg("a:1", "Я люблю джаз")], {}) == (0, 0)
    assert corpus.count() == 1 and corpus.cursors() == {"tg": "1", "web": "5"}
    assert corpus.db.execute("SELECT count(*) FROM refs").fetchone()[0] == 2
    assert corpus.timeline("p1")[0]["platform_message_id"] == "77"


def test_same_text_outside_window_or_other_person_or_role_is_a_new_row(corpus):
    base = msg("a:1", "hello there")
    far = msg("a:2", "hello there", ts=1000.0 + 5000)
    other_person = msg("a:3", "hello there", person="p2")
    other_role = msg("a:4", "hello there", role="assistant")
    assert corpus.add_batch([base, far, other_person, other_role], {}) == (4, 0)


def test_body_is_sealed_at_rest_and_timeline_roundtrips_in_order(corpus):
    corpus.add_batch([msg("a:2", "второе", ts=2.0), msg("a:1", "первое", ts=1.0)], {})
    raw = [r[0] for r in corpus.db.execute("SELECT body FROM messages")]
    assert raw and all(b.startswith("SEALED:") and "первое" not in b for b in raw)
    assert [m["text"] for m in corpus.timeline("p1")] == ["первое", "второе"]
    assert corpus.timeline("nobody") == []


def test_persons_are_scoped_and_listed(corpus):
    corpus.add_batch([msg("a:1", "x1", person="p1"), msg("a:2", "x2", person="p2"),
                      msg("a:3", "x3", person="p2")], {})
    assert [(p["person_key"], p["messages"]) for p in corpus.persons()] == [("p1", 1), ("p2", 2)]


def test_batch_is_atomic_cursor_not_advanced_when_insert_fails(corpus):
    bad = msg("a:9", "boom", ts=None)            # fails mid-batch inside the transaction
    with pytest.raises(TypeError):
        corpus.add_batch([msg("a:1", "ok message"), bad], {"tg": "9"})
    assert corpus.count() == 0 and corpus.cursors() == {}


def test_mark_analyzed_is_per_extractor_and_idempotent(corpus):
    corpus.add_batch([msg("a:1", "one"), msg("a:2", "two")], {})
    uids = [m["uid"] for m in corpus.timeline("p1")]
    corpus.mark_analyzed(uids[:1], "llm", "run1", [("fact1", "p1", uids[:1])])
    corpus.mark_analyzed(uids[:1], "llm", "run2")
    assert corpus.analyzed_uids("p1", "llm") == set(uids[:1])
    assert corpus.analyzed_uids("p1", "regex") == set()
    assert corpus.db.execute("SELECT count(*) FROM analyzed").fetchone()[0] == 1


def test_open_persists_and_in_memory_copy_never_touches_the_real_file(tmp_path):
    path = tmp_path / "sub" / "corpus.sqlite3"
    real = Corpus.open(path, seal, unseal)
    real.add_batch([msg("a:1", "persisted")], {})
    real.close()
    dry = Corpus.in_memory(seal, unseal, copy_from=path)
    assert dry.count() == 1
    dry.add_batch([msg("a:2", "only in the dry run")], {})
    dry.close()
    again = Corpus.open(path, seal, unseal)
    assert again.count() == 1
    again.close()
