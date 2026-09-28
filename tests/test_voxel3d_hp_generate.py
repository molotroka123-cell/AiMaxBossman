"""Bossman's local prompt -> part spec -> model loop (tools/voxel3d/hp_generate.py) with a fake LLM
and a fake vision critic: no Ollama, no GPU."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

pytest.importorskip("numpy")
pytest.importorskip("trimesh")
pytest.importorskip("fast_simplification")
pytest.importorskip("pygltflib")
pytest.importorskip("PIL")

from voxel3d import hp_generate, hpspec, meshgen  # noqa: E402

STOOL = {"name": "stool", "label": "Stool", "height": 0.6, "parts": [
    {"name": "seat", "shape": "lathe", "profile": [[0.3, 0.5], [0.3, 0.6]], "sections": 48, "color": "#9c6b3c",
     "surface": {"bumps": 0.005, "scale": 0.05}},
    {"name": "leg", "shape": "tube", "from": [0.2, 0.0, 0.0], "to": [0.2, 0.52, 0.0], "radius": 0.035,
     "repeat": {"count": 3}, "color": "#6e4a28"}]}
FLOATING = {**STOOL, "parts": STOOL["parts"] + [
    {"name": "cloud", "shape": "ellipsoid", "center": [0, 3, 0], "radii": 0.2, "color": "#dddddd"}]}


class FakeChat:
    def __init__(self, replies):
        self.replies = list(replies)
        self.seen: list[list[dict]] = []

    def __call__(self, messages):
        self.seen.append([dict(m) for m in messages])
        return self.replies.pop(0)


def test_few_shot_has_the_five_examples_and_system_documents_every_shape():
    shots = hp_generate.few_shot()
    assert len(shots) == 10 and all(m["role"] in ("user", "assistant") for m in shots)
    assert all(json.loads(m["content"])["parts"] for m in shots if m["role"] == "assistant")
    for shape in hpspec.SHAPES:
        assert f'"shape":"{shape}"' in hp_generate.SYSTEM


def test_errors_are_fed_back_until_valid_then_critique_drives_a_revision(tmp_path):
    better = json.loads(json.dumps(STOOL))
    better["parts"][0]["color"] = "#b5773d"
    chat = FakeChat(["this is not json", json.dumps(FLOATING), json.dumps(STOOL), json.dumps(better)])
    critiques = [{"looks_like": "a table", "recognisable": 2, "proportions": 3, "colours": 4, "fixes": ["shorter legs"]},
                 {"looks_like": "a stool", "recognisable": 5, "proportions": 4, "colours": 4, "fixes": []}]
    rep = hp_generate.generate("a wooden stool", tmp_path, chat, vision=lambda png, p: critiques.pop(0),
                               tries=3, rounds=2, log=lambda m: None, name="bm_stool")
    assert rep["ok"] and rep["model_calls"] == 4 and len(rep["versions"]) == 2
    assert rep["best_version"] == 2 and rep["critique"]["recognisable"] == 5
    assert 5000 <= rep["triangles"] <= 10000
    turns = [m["content"] for m in chat.seen[-1] if m["role"] == "user"]
    assert any("not one complete JSON" in t for t in turns)
    assert any("cloud floats" in t for t in turns)
    assert any("looks like: \"a table\"" in t and "shorter legs" in t for t in turns)
    d = tmp_path / "bm_stool"
    assert (d / "bm_stool.glb").is_file() and (d / "bm_stool.preview.png").is_file()
    assert json.loads((d / "bm_stool.spec.json").read_text())["parts"][0]["color"] == "#b5773d"


def test_stops_early_when_the_critic_is_satisfied(tmp_path):
    chat = FakeChat([json.dumps(STOOL)])
    good = {"looks_like": "a stool", "recognisable": 4, "proportions": 4, "colours": 3, "fixes": []}
    rep = hp_generate.generate("a stool", tmp_path, chat, vision=lambda png, p: good, rounds=2, log=lambda m: None)
    assert rep["ok"] and rep["model_calls"] == 1 and len(rep["versions"]) == 1


def test_keeps_the_best_version_when_a_revision_gets_worse(tmp_path):
    chat = FakeChat([json.dumps(STOOL), json.dumps(STOOL)])
    scores = [{"looks_like": "stool", "recognisable": 3, "proportions": 3, "colours": 3, "fixes": ["x"]},
              {"looks_like": "blob", "recognisable": 2, "proportions": 2, "colours": 2, "fixes": ["y"]}]
    rep = hp_generate.generate("a stool", tmp_path, chat, vision=lambda png, p: scores.pop(0), rounds=1, log=lambda m: None)
    assert rep["best_version"] == 1


def test_gives_up_cleanly_after_all_tries(tmp_path):
    rep = hp_generate.generate("a stool", tmp_path, FakeChat(["{}", "nope", "[]"]), tries=3, log=lambda m: None)
    assert rep["ok"] is False and (tmp_path / "bm_stool" / "report.json").is_file()


def test_validator_failures_become_actionable_advice():
    adv = hp_generate.check_advice({"budget_ok": True, "manifold_ok": False, "pivot_ok": True, "scale_ok": True,
                                    "colour_ok": False})
    assert len(adv) == 2 and "same place" in adv[0] and "saturated" in adv[1]


def test_duplicate_parts_are_reported():
    dup = {**STOOL, "parts": STOOL["parts"] + [dict(STOOL["parts"][0], name="seat2")]}
    _, _, errors = hpspec.build(hpspec.repair(dup)[0])
    assert any("seat and seat2 are in exactly the same place" in e for e in errors)


def test_parse_critique_clamps_and_survives_junk():
    c = hp_generate.parse_critique('{"looks_like": "a cart", "recognisable": 9, "proportions": "2", "fixes": ["a","b","c","d"]}')
    assert c["recognisable"] == 5 and c["proportions"] == 2 and c["colours"] == 1 and len(c["fixes"]) == 3
    assert hp_generate.parse_critique("garbage")["recognisable"] == 1


def test_cli_installs_into_a_game_only_with_apply(tmp_path, monkeypatch):
    game = tmp_path / "game"
    game.mkdir()
    (game / "project.godot").write_text("config_version=5\n")
    monkeypatch.setattr(hp_generate, "ollama_chat", lambda **kw: FakeChat([json.dumps(STOOL)]))
    assert hp_generate.main(["a stool", "--out", str(tmp_path / "o"), "--no-vision", "--game", str(game)]) == 0
    assert not (game / "assets").exists()
    monkeypatch.setattr(hp_generate, "ollama_chat", lambda **kw: FakeChat([json.dumps(STOOL)]))
    assert hp_generate.main(["a stool", "--out", str(tmp_path / "o"), "--no-vision", "--game", str(game), "--apply"]) == 0
    man = json.loads((game / "assets" / "props" / "props.json").read_text(encoding="utf-8"))
    assert "bm_stool" in man["props"] and man["props"]["bm_stool"]["method"].startswith("BOSSMAN LOCAL")


def test_examples_are_not_the_evaluation_prompts():
    prompts = " ".join(s.get("prompt", "") for s in meshgen.example_specs().values()).lower()
    for held_out in ("cart", "well", "boat", "windmill", "scarecrow"):
        assert held_out not in prompts
