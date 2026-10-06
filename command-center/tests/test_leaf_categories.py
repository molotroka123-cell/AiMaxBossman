"""authored_by_lane jeffb: behavior of the Jeff memory taxonomy (bcc.pit.categories). No mocks."""
from __future__ import annotations

from bcc.pit import categories


def test_all_keys_is_exactly_the_union_of_categories():
    union = {k for keys in categories.CATEGORIES.values() for k in keys}
    assert categories.ALL_KEYS == union
    assert isinstance(categories.ALL_KEYS, frozenset)
    assert len(categories.CATEGORIES) >= 10


def test_every_category_has_unique_snake_case_keys():
    for name, keys in categories.CATEGORIES.items():
        assert keys, name
        assert len(keys) == len(set(keys)), f"duplicate key inside {name}"
        for k in keys:
            assert k == k.lower() and " " not in k and k.replace("_", "").isalnum(), k


def test_core_memory_buckets_exist():
    for need in ("communication", "corrections", "goals", "assistant_usage", "visual_context"):
        assert need in categories.CATEGORIES
    assert "primary_language" in categories.CATEGORIES["communication"]
    assert "do_not_repeat" in categories.CATEGORIES["corrections"]


def test_no_sensitive_trait_buckets_are_presupposed():
    # consent-first memory: the taxonomy must not hard-code health/religion/politics/orientation buckets
    banned = ("health", "religion", "politic", "sexual", "orientation", "ethnic", "medical", "diagnos")
    for name, keys in categories.CATEGORIES.items():
        for token in (name, *keys):
            assert not any(b in token for b in banned), token


def test_relationships_are_self_reported_only():
    assert categories.CATEGORIES["relationships"][0] == "self_reported_relation_labels"
