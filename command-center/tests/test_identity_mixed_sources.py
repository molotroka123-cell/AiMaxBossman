"""Owner audit 01.11: bcc ran from a clean checkout while bossman_shared came from ANOTHER checkout (global editable
install) and /health/live still said PASS. A sibling package imported from outside this checkout is a mixed tree."""
from __future__ import annotations

import sys
import types
from pathlib import Path

from bcc import build_identity as bi

REPO = Path(bi.__file__).resolve().parents[2]


def _fake(monkeypatch, name: str, file: Path) -> None:
    mod = types.ModuleType(name)
    mod.__file__ = str(file)
    monkeypatch.setitem(sys.modules, name, mod)


def test_sibling_from_another_checkout_is_reported(monkeypatch, tmp_path):
    _fake(monkeypatch, "bossman_shared", tmp_path / "other-checkout" / "bossman_shared" / "__init__.py")
    _fake(monkeypatch, "bossman", REPO / "bossman-core" / "bossman" / "__init__.py")
    found = bi._foreign_packages()
    assert len(found) == 1 and found[0].startswith("bossman_shared")


def test_siblings_from_this_checkout_are_clean(monkeypatch):
    _fake(monkeypatch, "bossman_shared", REPO / "bossman_shared" / "__init__.py")
    _fake(monkeypatch, "bossman", REPO / "bossman-core" / "bossman" / "__init__.py")
    assert bi._foreign_packages() == []


def test_a_mixed_tree_is_never_pass(monkeypatch, tmp_path):
    _fake(monkeypatch, "bossman_shared", tmp_path / "x" / "bossman_shared" / "__init__.py")
    monkeypatch.setattr("bcc.run_provenance.repository_sha", lambda: "a" * 40)
    out = bi._resolve()
    assert out["source_identity"] == bi.UNKNOWN and out["build_sha"] is None and "смешанные источники" in out["detail"]
