"""Deterministic sentence extraction and scoring (bcc/collector/extract.py):
no model call, exact-offset quotes, zero overlap stays zero."""
from __future__ import annotations

from bcc.collector import extract


def test_sentence_offsets_are_exact():
    text = "First sentence here. Second one follows. Third and final."
    sentences = extract.split_sentences(text)
    assert len(sentences) == 3
    for s in sentences:
        assert text[s.offset:s.offset + len(s.text)] == s.text


def test_unrelated_sentence_scores_zero():
    score = extract.score_sentence("The weather today is sunny and mild.",
                                   {"amd", "ryzen", "bandwidth"})
    assert score == 0.0


def test_relevant_sentence_scores_above_zero():
    score = extract.score_sentence("AMD Ryzen memory bandwidth is high.",
                                   {"amd", "ryzen", "bandwidth"})
    assert score > 0.0


def test_candidate_facts_only_returns_relevant_sentences():
    text = ("AMD Ryzen AI Max+ 395 has a peak memory bandwidth of 256 GB/s. "
           "This paragraph is filler about gardening and weekend plans.")
    facts = extract.candidate_facts(subject="chip", text=text,
                                    topic="AMD Ryzen AI Max+ 395 memory bandwidth",
                                    url="http://x/y", retrieved_at_utc="2026-01-01T00:00:00Z",
                                    robots_allowed=True)
    assert len(facts) == 1
    assert "256 GB/s" in facts[0]["value"]
    assert facts[0]["predicate"] == "bandwidth"
    assert facts[0]["confidence"] > 0


def test_no_relevant_sentence_returns_empty():
    facts = extract.candidate_facts(subject="chip", text="Nothing about the topic at all here.",
                                    topic="AMD Ryzen AI Max+ 395 memory bandwidth", url="http://x",
                                    retrieved_at_utc="2026-01-01T00:00:00Z", robots_allowed=True)
    assert facts == []


def test_page_hash_is_stable_sha256():
    h1 = extract.page_sha256("same text")
    h2 = extract.page_sha256("same text")
    h3 = extract.page_sha256("different text")
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 64


def test_sentences_with_personal_contact_data_are_never_facts():
    text = ("The Widget X200 memory bandwidth is 256 GB/s over a 256-bit bus. "
           "Contact the author at jane.doe@example.com for Widget X200 questions. "
           "Call +1 555 0100 about the Widget X200 memory bandwidth. "
           "Widget X200 bandwidth questions: (555) 123-4567 or 555-123-4567.")
    facts = extract.candidate_facts(subject="Widget X200", text=text,
                                    topic="Widget X200 memory bandwidth",
                                    url="https://example.test/p", retrieved_at_utc="t",
                                    robots_allowed=True)
    assert [f["quote"] for f in facts] == [
        "The Widget X200 memory bandwidth is 256 GB/s over a 256-bit bus."]


def test_spec_numbers_are_not_mistaken_for_phone_numbers():
    for sentence in ("Peak bandwidth is 256 GB/s at 8000 MT/s.",
                     "It shipped 2024-2026 with 16 cores at 5.1 GHz.",
                     "LPDDR5X-8000 on a 256-bit bus gives 256 GB/s.",
                     "Die size is 307 mm2 on TSMC N4P, 120 W TDP."):
        assert not extract.has_personal_data(sentence), sentence
