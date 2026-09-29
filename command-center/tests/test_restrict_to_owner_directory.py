"""`_restrict_to_owner` on a directory must not lock the owner out of the files inside it (Windows DACL inheritance)."""
from __future__ import annotations

import os

import pytest

from bcc.auth import _restrict_to_owner

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows ACLs")


def test_children_stay_readable_after_the_directory_is_restricted(tmp_path):
    home = tmp_path / "telegram-calls"
    home.mkdir()
    fresh = home / "config.json"                 # never restricted by itself: it only inherits from the directory
    fresh.write_text("{}", encoding="utf-8")
    _restrict_to_owner(home)
    assert fresh.read_text(encoding="utf-8") == "{}"
    later = home / "worker.log"                  # created after the restriction
    later.write_text("x", encoding="utf-8")
    assert later.read_text(encoding="utf-8") == "x"
    _restrict_to_owner(fresh)                    # and a file can still be narrowed on its own afterwards
    assert fresh.read_text(encoding="utf-8") == "{}"
