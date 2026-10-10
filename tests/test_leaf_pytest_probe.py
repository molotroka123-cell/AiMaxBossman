"""The leaf probe maps a module to the tests that REALLY import it, and never guesses."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "tree_proof"))
import leaf_pytest_probe as p  # noqa: E402


def test_dotted_names_follow_the_import_roots():
    assert p.dotted("command-center/bcc/pit/discovery.py") == "bcc.pit.discovery"
    assert p.dotted("bossman-core/bossman/cognitive/context.py") == "bossman.cognitive.context"
    assert p.dotted("command-center/bcc/telegram_calls/__init__.py") == "bcc.telegram_calls"
    assert p.dotted("docs/x.md") is None and p.dotted("command-center/bcc/bad-name/x.py") is None


def test_only_real_imports_count(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("from bcc.pit import discovery\n", encoding="utf-8")
    (tmp_path / "tests" / "test_b.py").write_text("from bcc.pit.discovery import choose\n", encoding="utf-8")
    (tmp_path / "tests" / "test_c.py").write_text("import bcc.pit.discovery_other\n# discovery is mentioned in a comment\n", encoding="utf-8")
    (tmp_path / "tests" / "test_d.py").write_text("x = 'bcc.pit.discovery'\n", encoding="utf-8")
    got = p.importing_tests("bcc.pit.discovery", tmp_path)
    assert got == ["tests/test_a.py", "tests/test_b.py"]


def test_shared_sources_need_a_test_that_names_the_leaf(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("# gmail.search is exercised here\n", encoding="utf-8")
    (tmp_path / "tests" / "test_y.py").write_text("# nothing about it\n", encoding="utf-8")
    both = ["tests/test_x.py", "tests/test_y.py"]
    assert p.narrow_by_label(both, "gmail.search", tmp_path) == ["tests/test_x.py"]
    assert p.narrow_by_label(both, "drive.write", tmp_path) == []


def test_a_leaf_with_an_older_record_is_a_conflict_not_an_overwrite(tmp_path, monkeypatch):
    import json
    evid = tmp_path / "evidence"
    (evid / "out").mkdir(parents=True)
    (evid / "out" / "mod-x.txt").write_text("VERDICT FAIL (older probe)\n", encoding="utf-8")
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps({"nodes": [{"id": "mod-x", "parent": "z", "label": "x", "status": "code",
                                           "sources": [{"path": "command-center/bcc/pit/discovery.py"}]}]}), encoding="utf-8")
    monkeypatch.setattr(p, "SEED", seed)
    monkeypatch.setattr(p, "EVID", evid)
    p.main(["--leaf", "mod-x", "--lane", "t", "--python", "unused"])
    assert (evid / "out" / "mod-x.txt").read_text(encoding="utf-8") == "VERDICT FAIL (older probe)\n"
    assert not (evid / "t.json").exists()
