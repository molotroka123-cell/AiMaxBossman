"""Owner-approved source list parsing (bcc/collector/engine.py:load_source_list):
only http(s) URLs from the file itself — never a discovered link."""
from __future__ import annotations

import pytest

from bcc.collector.engine import SourceListError, load_source_list


def test_parses_urls_skips_comments_and_blanks(tmp_path):
    path = tmp_path / "sources.txt"
    path.write_text("# approved by owner 2026-09-28\n"
                    "https://en.wikipedia.org/wiki/Ryzen\n\n"
                    "  \n"
                    "https://ollama.com/library\n", encoding="utf-8")
    urls = load_source_list(path, max_pages=10)
    assert urls == ["https://en.wikipedia.org/wiki/Ryzen", "https://ollama.com/library"]


def test_rejects_non_http_scheme(tmp_path):
    path = tmp_path / "sources.txt"
    path.write_text("file:///etc/passwd\n", encoding="utf-8")
    with pytest.raises(SourceListError):
        load_source_list(path, max_pages=10)


def test_missing_file_is_reported(tmp_path):
    with pytest.raises(SourceListError):
        load_source_list(tmp_path / "does-not-exist.txt", max_pages=10)


def test_empty_list_is_reported(tmp_path):
    path = tmp_path / "sources.txt"
    path.write_text("# nothing here\n", encoding="utf-8")
    with pytest.raises(SourceListError):
        load_source_list(path, max_pages=10)


def test_deduplicates_preserving_order(tmp_path):
    path = tmp_path / "sources.txt"
    path.write_text("https://a.example/1\nhttps://a.example/1\nhttps://a.example/2\n",
                    encoding="utf-8")
    assert load_source_list(path, max_pages=10) == [
        "https://a.example/1", "https://a.example/2"]


def test_max_pages_truncates(tmp_path):
    path = tmp_path / "sources.txt"
    path.write_text("\n".join(f"https://a.example/{i}" for i in range(5)), encoding="utf-8")
    assert len(load_source_list(path, max_pages=2)) == 2


def test_max_pages_cannot_exceed_hard_ceiling(tmp_path):
    from bcc.collector import config
    path = tmp_path / "sources.txt"
    path.write_text("\n".join(f"https://a.example/{i}" for i in range(config.MAX_PAGES_CEILING + 20)),
                    encoding="utf-8")
    urls = load_source_list(path, max_pages=10_000)
    assert len(urls) == config.MAX_PAGES_CEILING
