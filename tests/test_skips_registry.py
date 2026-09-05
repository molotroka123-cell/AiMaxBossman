"""TRUTH-003 §16: реестр пропусков актуален и у каждого skip есть причина."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("skips_registry", ROOT / "tools" / "skips_registry.py")
reg = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(reg)  # type: ignore[union-attr]


def test_registry_is_current_and_every_skip_has_a_reason():
    rows = reg.collect()
    assert rows, "реестр пуст — сканер не нашёл ни одного skip"
    assert [r for r in rows if not r["reason"]] == []
    assert reg.OUT.read_text(encoding="utf-8") == reg.render(rows), "запустите: python tools/skips_registry.py"


def test_registry_rows_carry_owner_env_and_review():
    for r in reg.collect():
        assert r["owner"] and r["env"] and r["review"] and r["kind"] in ("skip", "skipif", "importorskip", "unparsable")
