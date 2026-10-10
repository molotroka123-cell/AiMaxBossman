"""Russian tree labels (owner 10.10): display text only — ids, parents and statuses are never touched, the original is kept."""
from __future__ import annotations

import json
from pathlib import Path

from tools.tree_proof import haiku_translate as t

ROOT = Path(__file__).resolve().parents[1]

SEED = ROOT / "command-center" / "bcc" / "capability_tree_seed.json"


def test_relabel_keeps_the_original_once_and_right_after_the_label():
    node = {"id": "x", "label": "bossman/toolkit/shell.py", "parent": "ops", "status": "code"}
    t.relabel(node, "Команды в песочнице")
    assert list(node) == ["id", "label", "label_en", "parent", "status"]
    assert node["label_en"] == "bossman/toolkit/shell.py"
    t.relabel(node, "Команды в изолированной среде")          # a second pass never overwrites the original
    assert node["label_en"] == "bossman/toolkit/shell.py" and node["label"] == "Команды в изолированной среде"


def test_only_russian_answers_are_accepted_and_the_root_keeps_its_name():
    assert t.clean("Gmail: отправить письмо") == "Gmail: отправить письмо"
    assert t.clean("gmail.send") is None and t.clean(None) is None and t.clean("") is None
    assert len(t.clean("Очень " * 40)) <= t.MAX_LABEL
    assert t.needs({"id": "bossman", "label": "Bossman"}) is False
    assert t.needs({"id": "plugin-12", "label": "gmail.send"}) is True
    assert t.needs({"id": "cap-7", "label": "Голос · STT · TTS"}) is False


def test_seed_labels_are_russian_unique_ids_and_originals_kept():
    nodes = json.loads(SEED.read_text(encoding="utf-8"))["nodes"]
    ids = [n["id"] for n in nodes]
    assert len(ids) == len(set(ids))
    for n in nodes:
        assert (n.get("label") or "").strip(), n["id"]
        if n["status"] != "retired" and n["id"] != "bossman":
            assert t.CYR.search(n["label"]), f"{n['id']} label is not Russian: {n['label']}"
        if "label_en" in n:
            assert n["label_en"].strip() and n["label_en"] != n["label"]
