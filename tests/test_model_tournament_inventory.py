"""tools/model_tournament_inventory.py: duplicate weights are ONE model, whatever the tag or the baked context says.

Pure functions on synthetic Modelfiles; no Ollama, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import model_tournament_inventory as inv  # noqa: E402

A = "a" * 64
B = "b" * 64


def _mf(blob: str) -> str:
    return f"# Modelfile\nFROM C:\\Users\\x\\.ollama\\models\\blobs\\sha256-{blob}\nPARAMETER num_ctx 65536\n"


def test_weights_blob_is_read_from_the_FROM_line():
    assert inv.weights_blob(_mf(A)) == A
    assert inv.weights_blob("FROM qwen3:8b\n") is None, "a registry name is not a local blob"
    assert inv.weights_blob("") is None


def test_baked_context_is_the_modelfile_value_not_a_guess():
    assert inv.baked_num_ctx("num_ctx                        65536\ntemperature   0.8") == 65536
    assert inv.baked_num_ctx("temperature 0.7") is None
    assert inv.baked_num_ctx("num_ctx abc") is None


def _row(name, blob, ctx, digest):
    return {"name": name, "weights_sha256": blob, "baked_num_ctx": ctx, "digest": digest * 3, "size_gb": 36.9}


def test_tags_with_the_same_weights_are_one_model_in_several_configurations():
    rows = [_row("community-ctx64k", A, 65536, "x"), _row("community-ctx256k", A, 262144, "y"),
            _row("main", B, 65536, "z")]
    groups = inv.group_by_weights(rows)
    dup = next(g for g in groups if g["duplicate"])
    assert dup["tags"] == ["community-ctx64k", "community-ctx256k"] and dup["weights_sha256"] == A
    assert [c["baked_num_ctx"] for c in dup["configurations"]] == [65536, 262144]
    assert sum(1 for g in groups if not g["duplicate"]) == 1


def test_a_tag_whose_blob_is_unknown_is_never_merged_with_another_unknown():
    rows = [_row("u1", None, None, "1"), _row("u2", None, None, "2")]
    groups = inv.group_by_weights(rows)
    assert len(groups) == 2 and not any(g["duplicate"] for g in groups)
