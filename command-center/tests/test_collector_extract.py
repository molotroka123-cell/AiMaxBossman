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
