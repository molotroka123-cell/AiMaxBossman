"""The per-leaf evidence registry must describe the same tree as the seed it is built from.

Regenerate with `python tools/tree_registry_sync.py registry ...` (never by hand). This test only
checks that the committed registry is not behind the seed and does not point at files that do not exist;
it does not raise any evidence level.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "command-center" / "bcc" / "capability_tree_seed.json"
REGISTRY = ROOT / "docs" / "architecture" / "tree-registry.json"


def _load():
    seed = {n["id"]: n for n in json.loads(SEED.read_text(encoding="utf-8"))["nodes"]}
    reg = {leaf["id"]: leaf for leaf in json.loads(REGISTRY.read_text(encoding="utf-8"))["leaves"]}
    return seed, reg


def test_every_registry_leaf_is_a_seed_node_with_the_same_status():
    seed, reg = _load()
    unknown = sorted(set(reg) - set(seed))
    stale = sorted(i for i in reg if i in seed and reg[i]["integration_status"] != seed[i]["status"])
    assert not unknown, f"registry leaves missing from the seed: {unknown[:10]}"
    assert not stale, f"{len(stale)} registry leaves carry a status older than the seed, e.g. {stale[:10]}"


def test_every_live_seed_leaf_has_a_registry_row():
    seed, reg = _load()
    parents = {n["parent"] for n in seed.values() if n.get("parent")}
    missing = sorted(i for i, n in seed.items()
                     if i not in parents and n["status"] != "retired" and i not in reg)
    assert not missing, f"{len(missing)} seed leaves have no registry row, e.g. {missing[:10]}"


def test_registry_test_files_exist():
    _, reg = _load()
    gone = sorted((i, t) for i, leaf in reg.items()
                  for t in leaf["levels"]["tests"].get("files", []) if not (ROOT / t).is_file())
    assert not gone, f"registry points at test files that do not exist: {gone[:10]}"
