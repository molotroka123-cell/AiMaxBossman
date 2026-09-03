import pytest

from osiris.provenance import PassportError, make_passport, validate_fact


def test_passport_ok():
    p = make_passport(source="wikidata", url="https://www.wikidata.org/wiki/Q1", method="sparql", license="CC0", confidence=0.9)
    f = validate_fact({"subject": "org:1", "predicate": "legal_name", "object": "X", "passport": p})
    assert f["passport"]["source"] == "wikidata"


def test_no_passport():
    with pytest.raises(PassportError):
        validate_fact({"subject": "org:1", "predicate": "legal_name", "object": "X"})


def test_bad_method():
    with pytest.raises(PassportError):
        make_passport(source="a", url="http://x", method="steal", license="x", confidence=1)
