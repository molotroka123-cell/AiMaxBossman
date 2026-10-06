"""Per-profile knowledge folders stay separate (zone memory, 2026-10-06).

new_profile_id(name) = safe_id(name)[:48] + "-<6 hex>". profile_root() ran the
id through safe_id() again, which truncates to 48 characters — for any profile
name of 48+ characters that cut off exactly the random suffix, so two different
profiles with the same long name got ONE shared knowledge folder.
"""
from __future__ import annotations

from bossman.profiles.memory import knowledge_dir, profile_root
from bossman.profiles.store import new_profile_id

LONG_NAME = "Family shared account for the kitchen tablet and the hallway"


def test_two_profiles_with_the_same_long_name_get_separate_folders(tmp_path):
    a, b = new_profile_id(LONG_NAME), new_profile_id(LONG_NAME)
    assert a != b
    assert knowledge_dir(tmp_path, a) != knowledge_dir(tmp_path, b)


def test_long_profile_root_is_stable_and_confined(tmp_path):
    pid = new_profile_id(LONG_NAME)
    root = profile_root(tmp_path, pid)
    assert root == profile_root(tmp_path, pid)
    assert root.parent == (tmp_path / "_profiles").resolve()
    assert len(root.name) <= 48


def test_short_profile_root_is_unchanged(tmp_path):
    assert profile_root(tmp_path, "guest-abc").name == "guest-abc"
