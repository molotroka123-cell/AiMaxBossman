"""tools/memory_inventory.py: classification by name, reversible candidates only, and no file is ever opened.

Synthetic directory trees under tmp_path; nothing from the owner's PC.
"""
from __future__ import annotations

import builtins
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import memory_inventory as mi  # noqa: E402

NOW = time.time()
OLD = NOW - 60 * 86400


def test_known_names_get_their_category():
    assert mi.classify("CommandCenter", NOW)[0] == "active_memory"
    assert mi.classify("CommandCenter.bak-20260927-0234", NOW)[0] == "duplicate", "a backup of the live dir is a duplicate, not active"
    assert mi.classify("owner-backup-20260925-pre15", OLD)[0] == "duplicate"
    assert mi.classify("pytest-model-market-20261004", OLD)[0] == "temp_cache"
    assert mi.classify("models", NOW)[0] == "useful_archive"
    assert mi.classify("telegram-companion", NOW)[0] == "active_memory"


def test_an_unrecognised_name_is_unknown_and_age_never_makes_it_a_duplicate():
    cat, why = mi.classify("something-new", OLD)
    assert cat == "unknown" and "owner's decision" in why
    cat, why = mi.classify("something-new", NOW)
    assert cat == "unknown" and "left alone" in why


def test_candidate_actions_are_reversible_and_small_entries_have_none():
    assert mi.candidate_action("duplicate", 10 * 2**20) is None
    big = 5 * 2**30
    plan = mi.candidate_action("duplicate", big)
    assert plan and "verify sha256" in plan and "keep the C: copy" in plan
    assert mi.candidate_action("active_memory", big) is None, "live memory is never a candidate"
    assert mi.candidate_action("unknown", big) is None


def test_inventory_counts_sizes_and_never_opens_a_file(tmp_path, monkeypatch):
    root = tmp_path / "Bossman"
    (root / "CommandCenter" / "chat").mkdir(parents=True)
    (root / "CommandCenter" / "bcc.db").write_bytes(b"x" * 1000)
    (root / "CommandCenter" / "chat" / "secret-chat.json").write_bytes(b"PRIVATE" * 10)
    (root / "CommandCenter.bak-1").mkdir()
    (root / "CommandCenter.bak-1" / "bcc.db").write_bytes(b"y" * 500)
    (root / "mystery").mkdir()
    opened = []
    real_open = builtins.open

    def spy(*a, **k):
        opened.append(a[0])
        return real_open(*a, **k)
    monkeypatch.setattr(builtins, "open", spy)
    doc = mi.inventory([root], now=NOW)
    monkeypatch.setattr(builtins, "open", real_open)
    assert opened == [], "inventory must read names/sizes only"
    by = {Path(e["path"]).name: e for e in doc["entries"]}
    assert by["CommandCenter"]["size_bytes"] == 1070 and by["CommandCenter"]["files"] == 2
    assert by["CommandCenter"]["category"] == "active_memory" and by["CommandCenter.bak-1"]["category"] == "duplicate"
    assert by["mystery"]["category"] == "unknown" and doc["content_read"] is False
    assert doc["totals"]["active_memory"]["entries"] == 1 and doc["totals"]["duplicate"]["entries"] == 1


def test_a_junction_cycle_does_not_hang_and_is_not_followed(tmp_path):
    """A first run on the owner PC spun 90+ minutes: Windows junctions look like plain directories to scandir."""
    root = tmp_path / "tree"
    (root / "real").mkdir(parents=True)
    (root / "real" / "a.bin").write_bytes(b"x" * 100)
    link = root / "real" / "loop"
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(root)], capture_output=True, text=True)
    if made.returncode != 0:
        pytest.skip("cannot create a junction here: " + made.stderr[:80])
    t0 = time.monotonic()
    meta = mi.scan(root, budget_s=20)
    assert time.monotonic() - t0 < 10, "the scan must not follow the junction back into the tree"
    assert meta["size_bytes"] == 100 and meta["files"] == 1 and meta["reparse_skipped"] == 1 and meta["complete"] is True


def test_a_scan_over_its_time_budget_says_it_is_incomplete(tmp_path):
    for i in range(50):
        (tmp_path / f"d{i}").mkdir()
        (tmp_path / f"d{i}" / "f.bin").write_bytes(b"z")
    meta = mi.scan(tmp_path, budget_s=-1)
    assert meta["complete"] is False, "an unfinished scan must not pose as a full measurement"
    doc = mi.inventory([tmp_path], now=NOW)
    assert doc["incomplete_entries"] == [] and all(e["complete"] for e in doc["entries"])
