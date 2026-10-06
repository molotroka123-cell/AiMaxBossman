"""authored_by_lane memapps: bossman.context_engine.plugins (read-only Markdown/JSON memory bridges)."""
from __future__ import annotations

import json

import pytest

from bossman.context_engine.models import MemoryKind, MemoryStatus
from bossman.context_engine.plugins import JsonMemoryPlugin, MarkdownMemoryPlugin


def test_markdown_plugin_retrieves_matching_blocks_only(tmp_path):
    (tmp_path / "n.md").write_text("- Owner likes dark theme\n\nUnrelated line about cats\n", encoding="utf-8")
    res = MarkdownMemoryPlugin(tmp_path).retrieve("dark theme", "p", 5)
    assert len(res) == 1 and "dark theme" in res[0].text
    assert res[0].status is MemoryStatus.ACTIVE and res[0].source_refs[0].startswith("file://")


def test_markdown_plugin_is_read_only_and_missing_root_is_empty(tmp_path):
    p = MarkdownMemoryPlugin(tmp_path / "nope")
    assert p.read_only is True and p.retrieve("x yz", "p", 3) == []
    with pytest.raises(RuntimeError):
        p.write_candidate(None)


def test_json_plugin_parses_kinds_and_falls_back_for_unknown(tmp_path):
    f = tmp_path / "m.json"
    f.write_text(json.dumps({"memories": [
        {"text": "prefer concise answers", "kind": "preference", "confidence": 0.9},
        {"text": "prefer concise logs", "kind": "no-such-kind"},
        {"text": "", "kind": "fact"},
    ]}), encoding="utf-8")
    res = JsonMemoryPlugin(f).retrieve("prefer concise", "p", 10)
    kinds = {r.text: r.kind for r in res}
    assert kinds["prefer concise answers"] is MemoryKind.PREFERENCE
    assert kinds["prefer concise logs"] is MemoryKind.SUMMARY
    assert len(res) == 2
    with pytest.raises(RuntimeError):
        JsonMemoryPlugin(f).write_candidate(None)
