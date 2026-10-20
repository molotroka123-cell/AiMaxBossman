"""Counterparty screening must not silently PASS a listed party. Synthetic names only, pure functions."""
from __future__ import annotations

import pytest

from warterminal import rules

LIST = ["ROSOBORONEXPORT", "Ростех", "Mesto Brno Logistics"]


@pytest.mark.parametrize("as_index", [False, True])
def test_cyrillic_name_is_screened_not_folded_to_empty(as_index):
    sanc = rules.SanctionsIndex(LIST) if as_index else LIST
    hit = rules.screen_party("Ростех", sanc)
    assert hit is not None and hit["exact"] and hit["entry"] == "Ростех"
    assert rules.screen_party("ООО «Ростех»", sanc)["exact"]          # legal form in Cyrillic is ignored


@pytest.mark.parametrize("as_index", [False, True])
def test_legal_form_or_extra_words_do_not_hide_a_listed_entity(as_index):
    sanc = rules.SanctionsIndex(LIST) if as_index else LIST
    assert rules.screen_party("OAO Rosoboronexport", sanc)["exact"]   # legal form only -> BLOCK
    assert rules.screen_party("JSC Rosoboronexport", sanc)["exact"]
    hit = rules.screen_party("PJSC Rosoboronexport Moscow Branch", sanc)
    assert hit is not None and not hit["exact"]                        # contains the entry -> human review


def test_unrelated_and_partial_names_keep_their_old_outcome():
    assert rules.screen_party("Totally Other Person", LIST) is None
    assert rules.screen_party("Statutární město Brno", ["Mesto Brno"]) is not None    # accents still folded
    hit = rules.screen_party("Mesto Brno", LIST)                                      # 2 of 3 entry tokens
    assert hit is not None and not hit["exact"]
