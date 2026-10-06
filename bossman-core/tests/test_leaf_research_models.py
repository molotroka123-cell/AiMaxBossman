"""authored_by_lane (opsplug): bossman.research.models - provenance-carrying research types."""
import dataclasses

import pytest

from bossman.research.models import (
    DEEP, MODES, QUICK, STANDARD, Claim, Evidence, ResearchMode, ResearchReport, Source,
)


def test_modes_are_ordered_by_cost_and_deep_is_only_reachable_by_name():
    assert (QUICK.max_sources, QUICK.max_rounds) == (3, 1)
    assert QUICK.max_sources < STANDARD.max_sources < DEEP.max_sources
    assert QUICK.max_rounds < STANDARD.max_rounds < DEEP.max_rounds
    assert MODES == {"quick": QUICK, "standard": STANDARD, "deep": DEEP}
    assert MODES["deep"].name == "deep"


def test_modes_evidence_and_claims_are_immutable():
    for obj in (QUICK, Evidence(Source("u"), "x", 1.0, "h"), Claim("t", (), 0.5), ResearchReport("q", QUICK, (), (), (), (), 0)):
        field = dataclasses.fields(obj)[0].name
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(obj, field, "changed")


def test_source_defaults_are_neutral_and_retrieval_is_stamped_later_by_the_engine():
    s = Source("https://example.org/a")
    assert (s.kind, s.trust, s.retrieved_at) == ("web", 0.5, None)
    s.retrieved_at = 12.5                                  # the only mutable record
    assert s.retrieved_at == 12.5


def test_a_report_keeps_provenance_chain_claim_to_evidence_to_source():
    src = Source("https://example.org/a", trust=0.9, retrieved_at=1.0)
    ev = Evidence(src, "verbatim excerpt", 1.0, "abc123")
    claim = Claim("the sky is blue", (ev,), confidence=0.8)
    rep = ResearchReport("why is the sky blue?", STANDARD, (claim,), (src,), (), ("cause of red sunsets",), rounds_used=1)
    assert rep.claims[0].evidence[0].source is src and rep.claims[0].evidence[0].excerpt == "verbatim excerpt"
    assert rep.rounds_used < rep.mode.max_rounds           # early stop is representable
    assert rep.unanswered == ("cause of red sunsets",) and rep.fetch_errors == () and claim.contradicted is False


def test_research_mode_is_a_value_object():
    assert ResearchMode("quick", 3, 1) == QUICK
    assert hash(ResearchMode("quick", 3, 1)) == hash(QUICK)
